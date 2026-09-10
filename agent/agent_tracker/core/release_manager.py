from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse
import requests
from .files import atomic_json, inside, read_json
from .signed_updates import (verify_manifest, stage_archive, MAX_ARCHIVE_BYTES, MAX_UNPACKED_BYTES,
                             runtime_compatible, release_environment_headers)


def executable_name(native=False, bootstrap=False):
    if os.name == "nt":
        return ("SoftTrackingHost" if native else "SoftTracking") + ("" if bootstrap else "App") + ".exe"
    return ("soft-tracking-host" if native else "soft-tracking") + ("" if bootstrap else "-app")


def release_path(root, version):
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version):
        raise ValueError("Invalid installed version")
    return inside(root, Path(root) / "versions" / version)


class ReleaseManager:
    def __init__(self, root, session=None):
        self.root = Path(root).resolve()
        self.session = session or requests.Session()
        self.keys = read_json(self.root / "trusted-update-keys.json", {})
        self._environment_headers()

    def _environment_headers(self):
        headers = release_environment_headers()
        session_headers = getattr(self.session, 'headers', None)
        if session_headers is not None:
            for name in ('X-Tracking-OS', 'X-Tracking-OS-Version', 'X-Tracking-GLIBC', 'X-Tracking-Update-Protocol'):
                session_headers.pop(name, None)
            session_headers.update(headers)
        return headers

    def active(self):
        value = read_json(self.root / "current.json")
        if not isinstance(value, dict):
            raise ValueError("Installation has no active version")
        release_path(self.root, value["version"])
        return value

    def check(self):
        active = self.active()
        build = read_json(release_path(self.root, active["version"]) / "build.json")
        target = build["target"]
        if not re.fullmatch(r"(?:windows|macos|linux)-(?:x86|x64|arm64)-(?:modern|legacy)", target):
            raise ValueError("Invalid release target")
        url = "https://tracking.salesoftech.com/client/v3/releases/" + target + "/manifest.json"
        headers = dict(self._environment_headers(), **{'X-Tracking-Client-Version': active['version']})
        with self.session.get(url, timeout=(10, 30), allow_redirects=False, stream=True, headers=headers) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise ValueError("Release server unavailable")
            raw = bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise ValueError("Release manifest too large")
        envelope = json.loads(raw)
        # No-change responses still require a valid signature; verification accepts
        # a development floor, then comparisons enforce the installed high-water mark.
        manifest = verify_manifest(envelope, self.keys, "0.0.0", build["os"], build["architecture"])
        if manifest.get("target") != target:
            raise ValueError("Wrong runtime profile")
        if not runtime_compatible(manifest):
            return None
        from packaging.version import Version
        if Version(manifest["version"]) <= Version(active["version"]) or manifest["version"] == read_json(self.root / "failed.json", {}).get("version"):
            return None
        return manifest

    def download(self, manifest):
        if not runtime_compatible(manifest):
            raise ValueError('Update requires a newer operating system; current version retained')
        url=urlparse(manifest['url'])
        if url.scheme!='https' or url.hostname!='tracking.salesoftech.com' or url.port not in (None,443) or url.username or url.password:
            raise ValueError('Release credentials cannot be sent to another origin')
        if shutil.disk_usage(self.root).free < manifest['size'] + MAX_UNPACKED_BYTES + 128*1024*1024:
            raise ValueError('Update postponed: insufficient free disk space; activity queue retained')
        downloads = self.root / "downloads"
        downloads.mkdir(exist_ok=True, mode=0o700)
        archive = downloads / (uuid.uuid4().hex + ".zip")
        size = 0
        digest = hashlib.sha256()
        started = time.monotonic()
        try:
            with self.session.get(manifest["url"], timeout=(10, 30), stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError("Release download failed")
                with archive.open("xb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > min(manifest["size"], MAX_ARCHIVE_BYTES) or time.monotonic() - started > 600:
                            raise ValueError("Release download limit exceeded")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            if size != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
                raise ValueError("Release checksum mismatch")
            return archive
        except Exception:
            archive.unlink(missing_ok=True)
            raise

    def stage(self, archive, manifest):
        if not runtime_compatible(manifest):
            raise ValueError('Update requires a newer operating system; current version retained')
        destination = self.root / "staging" / uuid.uuid4().hex
        stage_archive(archive, manifest, destination)
        build = read_json(destination / "build.json")
        if not build or build.get("version") != manifest["version"] or build.get("target") != manifest["target"] or build.get("launcher_protocol") != 1:
            raise ValueError("Incompatible release layout")
        for native in (False, True):
            if not (destination / "app" / executable_name(native)).is_file():
                raise ValueError("Release is missing an executable")
        extension = read_json(destination / "extension" / "manifest.json")
        installed = read_json(self.root / "extension" / "manifest.json")
        if not extension or extension.get("key") != installed.get("key"):
            raise ValueError("Extension identity changed")
        # New permissions require a deliberate installer upgrade/browser consent.
        for field in ("permissions", "host_permissions"):
            if set(extension.get(field, [])) - set(installed.get(field, [])):
                raise ValueError("Extension permissions require manual approval")
        return destination

    def activate(self, staged):
        staged = inside(self.root / "staging", staged)
        build = read_json(staged / "build.json")
        old = self.active()
        destination = release_path(self.root, build["version"])
        if destination.exists():
            orphan = self.root / "orphaned" / (destination.name + '-' + uuid.uuid4().hex)
            orphan.parent.mkdir(exist_ok=True)
            if old['version']==build['version']:
                raise ValueError('Cannot replace the active version')
            os.replace(str(destination),str(orphan))
        destination.parent.mkdir(exist_ok=True)
        os.replace(str(staged), str(destination))
        backup = self.root / ("extension-backup-" + uuid.uuid4().hex)
        incoming = self.root / ("extension-incoming-" + uuid.uuid4().hex)
        shutil.copytree(destination / "extension", incoming)
        journal = {"previous": old, "version": build["version"], "backup": backup.name, "incoming": incoming.name}
        atomic_json(self.root / "pending.json", journal)
        try:
            os.replace(str(self.root / "extension"), str(backup))
            os.replace(str(incoming), str(self.root / "extension"))
            atomic_json(self.root / "current.json", {"version": build["version"]})
        except Exception:
            self.rollback()
            raise
        return journal

    def confirm(self):
        pending = read_json(self.root / "pending.json")
        if pending:
            atomic_json(self.root / "last-good.json", dict(self.active(), previous_version=pending['previous']['version']))
            (self.root / "pending.json").unlink()

    def rollback(self):
        pending = read_json(self.root / "pending.json")
        if not pending:
            return
        if read_json(self.root/'last-good.json',{}).get('version')==pending['version']:
            (self.root/'pending.json').unlink()
            return
        backup = inside(self.root, self.root / pending["backup"])
        if backup.is_dir():
            current = self.root / "extension"
            if current.exists():
                os.replace(str(current), str(self.root / ("extension-failed-" + uuid.uuid4().hex)))
            os.replace(str(backup), str(current))
        atomic_json(self.root / "current.json", pending["previous"])
        atomic_json(self.root / "failed.json", {"version": pending["version"], "time": int(time.time())})
        (self.root / "pending.json").unlink()
