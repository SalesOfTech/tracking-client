from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from nacl.signing import VerifyKey
from packaging.version import Version


MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MAX_UNPACKED_BYTES = 750 * 1024 * 1024


def _system_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,6}(?:\.\d{1,6}){0,3}', value):
        raise ValueError('Invalid release platform version')
    parts = tuple(int(part) for part in value.split('.'))
    return parts + (0,) * (4 - len(parts))


def validate_runtime_requirements(manifest):
    for field in ('minimum_os_version', 'minimum_glibc'):
        if field in manifest:
            _system_version(manifest[field])
    if 'minimum_os_version' in manifest and manifest.get('os') not in ('windows', 'macos'):
        raise ValueError('OS minimum is only defined for Windows and macOS')
    if 'minimum_glibc' in manifest and manifest.get('os') != 'linux':
        raise ValueError('glibc minimum is only defined for Linux')
    if 'requires_compatibility_gate' in manifest and type(manifest['requires_compatibility_gate']) is not bool:
        raise ValueError('Invalid compatibility gate')
    if manifest.get('ui') == 'electron' or manifest.get('requires_compatibility_gate'):
        required = 'minimum_glibc' if manifest.get('os') == 'linux' else 'minimum_os_version'
        if required not in manifest:
            raise ValueError('Release requires an explicit platform minimum')
    if manifest.get('ui') == 'electron' and manifest.get('architecture') not in ('x64', 'arm64'):
        raise ValueError('Electron release requires a 64-bit architecture')


def runtime_release_environment():
    """Unknown OS/libc versions stay unknown; never use a kernel as a macOS version."""
    result = {'os': {'Windows': 'windows', 'Darwin': 'macos', 'Linux': 'linux'}.get(platform.system(), '')}
    try:
        if result['os'] == 'macos':
            result['os_version'] = platform.mac_ver()[0]
        elif result['os'] == 'windows':
            result['os_version'] = platform.win32_ver()[1]
        elif result['os'] == 'linux':
            result['libc'], result['libc_version'] = platform.libc_ver()
    except (OSError, ValueError, AttributeError):
        pass
    return result


def runtime_compatible(manifest, environment=None):
    validate_runtime_requirements(manifest)
    if not any(field in manifest for field in ('minimum_os_version', 'minimum_glibc')):
        return True  # Historical Qt manifests keep their existing target checks.
    environment = runtime_release_environment() if environment is None else environment
    if environment.get('os') != manifest.get('os'):
        return False
    try:
        if 'minimum_os_version' in manifest:
            return _system_version(environment.get('os_version')) >= _system_version(manifest['minimum_os_version'])
        return (environment.get('libc') == 'glibc' and
                _system_version(environment.get('libc_version')) >= _system_version(manifest['minimum_glibc']))
    except ValueError:
        return False


def release_environment_headers():
    # Capability hints for catalog filtering, not authentication or OS attestation.
    environment = runtime_release_environment()
    headers = {'X-Tracking-Update-Protocol': '1'}
    if environment.get('os') in ('windows', 'macos', 'linux'):
        headers['X-Tracking-OS'] = environment['os']
    fields = []
    if environment.get('os') in ('windows', 'macos'):
        fields.append(('os_version', 'X-Tracking-OS-Version'))
    elif environment.get('os') == 'linux' and environment.get('libc') == 'glibc':
        fields.append(('libc_version', 'X-Tracking-GLIBC'))
    for field, header in fields:
        try:
            _system_version(environment.get(field))
        except ValueError:
            continue
        headers[header] = environment[field]
    return headers


def verify_manifest(envelope: dict, trusted_keys: dict, current_version: str, target_os: str, architecture: str, now=None) -> dict:
    """The keyring is shipped with the client, never downloaded beside the manifest."""
    key_id = envelope.get("key_id")
    if key_id not in trusted_keys:
        raise ValueError("Unknown release signing key")
    raw = base64.b64decode(envelope["payload"], validate=True)
    if len(raw) > 32 * 1024:
        raise ValueError("Release manifest too large")
    signature = base64.b64decode(envelope["signature"], validate=True)
    VerifyKey(base64.b64decode(trusted_keys[key_id], validate=True)).verify(raw, signature)
    manifest = json.loads(raw)
    now = time.time() if now is None else now
    if manifest.get("protocol") != 3 or manifest.get("os") != target_os or manifest.get("architecture") != architecture:
        raise ValueError("Wrong release target")
    if not isinstance(manifest.get("expires_at"), int) or manifest["expires_at"] < now:
        raise ValueError("Expired release manifest")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", manifest.get("version", "")) or Version(manifest["version"]) <= Version(current_version):
        raise ValueError("Update is not a newer version")
    url = urlparse(manifest.get("url", ""))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("Release download must use HTTPS")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("sha256", "")):
        raise ValueError("Invalid archive checksum")
    if not isinstance(manifest.get("size"), int) or not 0 < manifest["size"] <= MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid archive size")
    validate_runtime_requirements(manifest)
    return manifest


def stage_archive(archive: Path, manifest: dict, destination: Path) -> Path:
    """Validate all entries before writing. Activation is a separate release gate."""
    if not runtime_compatible(manifest):
        raise ValueError('Update requires a newer operating system; current version retained')
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size != manifest["size"]:
        raise ValueError("Archive size mismatch")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != manifest["sha256"]:
        raise ValueError("Archive checksum mismatch")
    if destination.exists():
        raise ValueError("Staging directory already exists")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 20000 or sum(item.file_size for item in entries) > MAX_UNPACKED_BYTES:
            raise ValueError("Archive expansion limit exceeded")
        seen = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            segments = item.filename.rstrip("/").split("/")
            mode = item.external_attr >> 16
            names = {"con", "prn", "aux", "nul"} | {"com" + str(i) for i in range(1, 10)} | {"lpt" + str(i) for i in range(1, 10)}
            if path.is_absolute() or "\\" in item.filename or ":" in item.filename or any(p in ("", ".", "..") or p.endswith((".", " ")) or p.split(".")[0].lower() in names for p in segments) or stat.S_ISLNK(mode):
                raise ValueError("Unsafe archive path")
            normalized = item.filename.rstrip("/").lower()
            if normalized in seen or item.flag_bits & 1:
                raise ValueError("Duplicate/encrypted archive entry")
            seen.add(normalized)
        destination.mkdir(parents=True, mode=0o700)
        for item in entries:
            target = destination.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, target.open("xb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o700 if (item.external_attr >> 16) & 0o111 else 0o600)
    return destination
