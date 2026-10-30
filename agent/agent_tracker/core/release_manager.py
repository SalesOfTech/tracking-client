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
from .endpoints import EndpointTransport, trusted_tracking_url
from .files import atomic_json, inside, read_json
from .signed_updates import (verify_manifest, stage_archive, MAX_ARCHIVE_BYTES, MAX_UNPACKED_BYTES,
                             runtime_compatible, release_environment_headers)

RETRY_DELAYS = (900, 3600, 14400, 86400)


def retry_at(failure):
    if not isinstance(failure, dict):
        return 0
    value = failure.get('retry_at')
    if type(value) is int and 0 <= value <= time.time() + RETRY_DELAYS[-1]:
        return value
    # Old clients wrote only version/time. They must not blacklist a release forever.
    return 0


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
        self.transport = EndpointTransport(self.session)
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
        with self.transport.request('get', url, timeout=(10, 30), stream=True, headers=headers) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise requests.HTTPError("Release server unavailable", response=response)
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
        failure = read_json(self.root / 'failed.json', {})
        if Version(manifest["version"]) <= Version(active["version"]):
            return None
        if manifest['version'] == failure.get('version') and time.time() < retry_at(failure):
            return None
        return manifest

    def download(self, manifest):
        if not runtime_compatible(manifest):
            raise ValueError('Update requires a newer operating system; current version retained')
        url=urlparse(manifest['url'])
        if not trusted_tracking_url(manifest['url']):
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
            with self.transport.request('get', manifest["url"], timeout=(10, 30), stream=True) as response:
                if response.status_code != 200:
                    raise requests.HTTPError("Release download failed", response=response)
                with archive.open("xb") as output:
                    for chunk in response.iter_content(1024 * 1024):
                        if (self.root / 'uninstall-requested.json').exists():
                            raise ValueError('Update cancelled for application removal')
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
        try:
            stage_archive(archive, manifest, destination)
            self._validate_staged(destination, manifest)
            return destination
        except Exception:
            self.discard_stage(destination)
            raise

    def discard_stage(self, destination):
        destination = inside(self.root / 'staging', destination)
        if destination.exists():
            shutil.rmtree(destination)

    def _validate_staged(self, destination, manifest):
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

    def _failed_artifact_path(self, entry):
        version = entry['version']
        release_path(self.root, version)
        name = entry['path']
        if not isinstance(name, str) or not re.fullmatch(
                r'(?:orphaned/' + re.escape(version) + r'-|extension-failed-)[a-f0-9]{32}', name):
            raise ValueError('Invalid failed artifact')
        path = self.root / name
        # Do not follow replacement symlinks/junctions, including the orphaned parent.
        if path.resolve() != path or path.is_symlink():
            raise ValueError('Redirected failed artifact')
        return path

    def _remember_failed_artifact(self, path, version):
        try:
            entry = {'path': path.relative_to(self.root).as_posix(), 'version': version}
            self._failed_artifact_path(entry)
            journal = self.root / 'failed-artifacts.json'
            entries = read_json(journal, [])
            if not isinstance(entries, list):
                return
            if entry not in entries:
                atomic_json(journal, entries + [entry])
        except (OSError, ValueError, TypeError, KeyError):
            # Unrecorded artifacts are retained, never rediscovered by directory glob.
            pass

    def _failed_artifact_in_use(self, path, version):
        import psutil
        try:
            owner = psutil.Process().username()
            roots = [path]
            if path.parent == self.root / 'orphaned':
                # An OS/process cache may still report the pre-rename executable path.
                roots.append(release_path(self.root, version))
            for item in psutil.process_iter():
                try:
                    process = psutil.Process(item.pid)
                    if process.username() != owner:
                        continue
                    arguments = [process.exe()] + process.cmdline()
                    for argument in arguments:
                        candidate = Path(argument.split('=', 1)[-1])
                        if candidate.is_absolute():
                            candidate = candidate.resolve()
                            if any(candidate == root or root in candidate.parents for root in roots):
                                return True
                except psutil.NoSuchProcess:
                    continue
            return False
        except (psutil.Error, OSError, ValueError):
            # Incomplete process visibility is not evidence that deletion is safe.
            return True

    def _cleanup_failed_artifacts(self):
        try:
            # Called only at activation/rollback boundaries, never during a pending switch.
            if (self.root / 'pending.json').exists():
                return
            good = read_json(self.root / 'last-good.json', {})
            protected = {self.active()['version'], good.get('version'), good.get('previous_version')}
            protected_paths = [release_path(self.root, version) for version in protected if version is not None]
            journal = self.root / 'failed-artifacts.json'
            entries = read_json(journal, [])
            if not isinstance(entries, list):
                return
            remaining = []
            for entry in entries:
                try:
                    path = self._failed_artifact_path(entry)
                    referenced = any(path == live or path in live.parents or live in path.parents
                                     for live in protected_paths)
                    if entry['version'] in protected or referenced or self._failed_artifact_in_use(path, entry['version']):
                        remaining.append(entry)
                        continue
                    if path.exists():
                        shutil.rmtree(path)
                except (OSError, ValueError, TypeError, KeyError):
                    remaining.append(entry)
            if remaining != entries:
                atomic_json(journal, remaining)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            pass

    def activate(self, staged):
        staged = inside(self.root / "staging", staged)
        build = read_json(staged / "build.json")
        old = self.active()
        self._cleanup_failed_artifacts()
        destination = release_path(self.root, build["version"])
        if destination.exists():
            orphan = self.root / "orphaned" / (destination.name + '-' + uuid.uuid4().hex)
            orphan.parent.mkdir(exist_ok=True)
            if old['version']==build['version']:
                raise ValueError('Cannot replace the active version')
            failure = read_json(self.root / 'failed.json', {})
            if failure.get('version') == build['version']:
                self._remember_failed_artifact(orphan, build['version'])
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
            (self.root / 'failed.json').unlink(missing_ok=True)
            if os.name == 'nt':
                from ..uninstaller import register_uninstaller
                try:
                    register_uninstaller(self.root)
                except (OSError, ValueError, KeyError):
                    # A locked uninstall launcher must not roll back a healthy update.
                    try:
                        atomic_json(self.root / 'uninstaller-status.json', {'state': 'repair_required'})
                    except OSError:
                        pass

    def rollback(self):
        pending = read_json(self.root / "pending.json")
        if not pending:
            return False
        if read_json(self.root/'last-good.json',{}).get('version')==pending['version']:
            (self.root/'pending.json').unlink()
            return False
        backup = inside(self.root, self.root / pending["backup"])
        if backup.is_dir():
            current = self.root / "extension"
            if current.exists():
                failed_extension = self.root / ("extension-failed-" + uuid.uuid4().hex)
                self._remember_failed_artifact(failed_extension, pending['version'])
                os.replace(str(current), str(failed_extension))
            os.replace(str(backup), str(current))
        atomic_json(self.root / "current.json", pending["previous"])
        failure = read_json(self.root / 'failed.json', {})
        attempts = failure.get('attempts', 0) if failure.get('version') == pending['version'] else 0
        attempts = min(attempts, 31) if type(attempts) is int and attempts >= 0 else 0
        now = int(time.time())
        atomic_json(self.root / "failed.json", {"version": pending["version"], "time": now,
                    "attempts": attempts + 1, "retry_at": now + RETRY_DELAYS[min(attempts, len(RETRY_DELAYS) - 1)]})
        (self.root / "pending.json").unlink()
        self._cleanup_failed_artifacts()
        return True
