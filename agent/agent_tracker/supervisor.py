from __future__ import annotations
import os
import subprocess
import time
import uuid
from packaging.version import Version
from pathlib import Path
from .core.files import atomic_json, read_json
from .core.release_manager import ReleaseManager, release_path, executable_name
from .core.client import ClientState, workspace
from .core.update_control import publish_status, take_request


def main(root, autostart=False):
    root = Path(root).resolve()
    uninstall_marker = root / 'uninstall-requested.json'
    # Retained 3.2.0 launchers do not know this marker. Exit without retry (75)
    # or creating client state when they enter the current supervisor payload.
    if uninstall_marker.exists():
        return 0
    manager = ReleaseManager(root)
    credentials = ClientState(root.parent)
    try:
        manager.session.headers['Authorization'] = 'Bearer ' + credentials.get('device')['device_secret']
    finally:
        credentials.close()
    # A pending journal on a fresh supervisor start means power loss or a failed launch.
    if (root / "pending.json").exists():
        manager.rollback()
    token = uuid.uuid4().hex
    run = root / "run"
    run.mkdir(exist_ok=True, mode=0o700)
    health = run / (token + ".ready")
    executable = release_path(root, manager.active()["version"]) / "app" / executable_name()
    env = dict(os.environ, SOFT_TRACKING_INSTALL=str(root), SOFT_TRACKING_HEALTH=str(health), SOFT_TRACKING_RUN_TOKEN=token)
    args = [str(executable)] + (["--autostart"] if autostart else [])
    if uninstall_marker.exists():
        return 0
    child = subprocess.Popen(args, env=env)
    next_check = time.monotonic()
    while child.poll() is None:
        time.sleep(1)
        if uninstall_marker.exists():
            # The uninstaller requests a graceful child stop and waits for our lock.
            continue
        request_id = take_request(root)
        if not request_id and time.monotonic() < next_check:
            continue
        next_check = time.monotonic() + 3600
        try:
            publish_status(root, 'checking', request_id)
            state=ClientState(root.parent)
            try:
                if not state.get('identity'):
                    publish_status(root, 'registration', request_id)
                    next_check=time.monotonic()+60
                    continue
                manager.session.headers['Authorization']='Bearer '+state.get('device')['device_secret']
            finally:
                state.close()
            manifest = manager.check()
            if not manifest:
                failed = read_json(root / 'failed.json', {})
                active_version = manager.active()['version']
                failed_version = failed.get('version')
                unresolved = failed_version and Version(failed_version) > Version(active_version)
                publish_status(root, 'rolled_back' if unresolved else 'active', request_id,
                               version=active_version, failed_version=failed_version if unresolved else None)
                continue
            publish_status(root, 'downloading', request_id, version=manifest['version'])
            archive = manager.download(manifest)
            staged = manager.stage(archive, manifest)
            archive.unlink()
            atomic_json(root / "stop-request.json", {"token":token})
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                (root / "stop-request.json").unlink(missing_ok=True)
                raise ValueError("Update postponed: application is still saving activity")
            manager.activate(staged)
            (root / 'stop-request.json').unlink(missing_ok=True)
            new_health = run / (uuid.uuid4().hex + ".ready")
            env["SOFT_TRACKING_HEALTH"] = str(new_health)
            newer = release_path(root, manifest["version"]) / "app" / executable_name()
            if uninstall_marker.exists():
                manager.rollback()
                return 0
            candidate = subprocess.Popen([str(newer), "--health-check"], env=env)
            deadline = time.monotonic() + 45
            while candidate.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if candidate.poll() is not None and candidate.returncode == 0 and new_health.is_file():
                manager.confirm()
                publish_status(root, 'installed', request_id, version=manifest['version'])
            else:
                # This is our short-lived self-test process, never a user's browser.
                if candidate.poll() is None:
                    candidate.terminate()
                    candidate.wait(timeout=10)
                manager.rollback()
                publish_status(root, 'rolled_back', request_id, version=manifest['version'])
            (root / "stop-request.json").unlink(missing_ok=True)
            return 75
        except Exception as error:
            if child.poll() is not None:
                manager.rollback()
                return 75
            publish_status(root, 'error', request_id, message=str(error))
            next_check = time.monotonic() + 300
    return child.returncode
