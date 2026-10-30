from __future__ import annotations
import os
import subprocess
import time
import uuid
from packaging.version import Version
from pathlib import Path
from .core.files import atomic_json, read_json
from .core.release_manager import ReleaseManager, release_path, executable_name, retry_at
from .core.client import ClientState, workspace
from .core.update_control import publish_status, take_request
from .core.update_diagnostics import report_failure, flush_reports


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
        pending = read_json(root / 'pending.json', {})
        try:
            if manager.rollback():
                report_failure(root, 'recovery', pending.get('version', 'unknown'))
        except Exception as error:
            report_failure(root, 'recovery', pending.get('version', 'unknown'), error)
            raise
        finally:
            flush_reports(root)
    token = uuid.uuid4().hex
    run = root / "run"
    run.mkdir(exist_ok=True, mode=0o700)
    health = run / (token + ".ready")
    executable = release_path(root, manager.active()["version"]) / "app" / executable_name()
    env = dict(os.environ, SOFT_TRACKING_INSTALL=str(root), SOFT_TRACKING_HEALTH=str(health), SOFT_TRACKING_RUN_TOKEN=token)
    args = [str(executable)] + (["--autostart"] if autostart else [])
    if uninstall_marker.exists():
        return 0
    try:
        child = subprocess.Popen(args, env=env)
    except Exception as error:
        report_failure(root, 'launch', manager.active()['version'], error)
        flush_reports(root)
        raise
    next_check = time.monotonic()
    next_report = next_check
    while child.poll() is None:
        time.sleep(1)
        if uninstall_marker.exists():
            # The uninstaller requests a graceful child stop and waits for our lock.
            continue
        if time.monotonic() >= next_report:
            flush_reports(root)
            next_report = time.monotonic() + 300
        request_id = take_request(root)
        if not request_id and time.monotonic() < next_check:
            continue
        next_check = time.monotonic() + 3600
        stage, target_version = 'check', 'unknown'
        staged = None
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
                retry = retry_at(failed) if unresolved else 0
                if retry > time.time():
                    next_check = time.monotonic() + min(3600, max(1, retry - time.time()))
                publish_status(root, 'rolled_back' if unresolved else 'active', request_id,
                               version=active_version, failed_version=failed_version if unresolved else None,
                               retry_at=retry)
                continue
            target_version = manifest['version']
            stage = 'download'
            publish_status(root, 'downloading', request_id, version=manifest['version'])
            archive = manager.download(manifest)
            stage = 'stage'
            try:
                staged = manager.stage(archive, manifest)
            finally:
                archive.unlink()
            stage = 'stop'
            atomic_json(root / "stop-request.json", {"token":token})
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                (root / "stop-request.json").unlink(missing_ok=True)
                raise ValueError("Update postponed: application is still saving activity")
            stage = 'activate'
            manager.activate(staged)
            (root / 'stop-request.json').unlink(missing_ok=True)
            new_health = run / (uuid.uuid4().hex + ".ready")
            env["SOFT_TRACKING_HEALTH"] = str(new_health)
            newer = release_path(root, manifest["version"]) / "app" / executable_name()
            if uninstall_marker.exists():
                manager.rollback()
                return 0
            stage = 'health'
            candidate = subprocess.Popen([str(newer), "--health-check"], env=env)
            deadline = time.monotonic() + 45
            while candidate.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if candidate.poll() is not None and candidate.returncode == 0 and new_health.is_file():
                stage = 'confirm'
                manager.confirm()
                publish_status(root, 'installed', request_id, version=manifest['version'])
            else:
                stage = 'health_timeout' if candidate.poll() is None else 'health_exit' if candidate.returncode != 0 else 'health_receipt'
                # This is our short-lived self-test process, never a user's browser.
                if candidate.poll() is None:
                    candidate.terminate()
                    try:
                        candidate.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        candidate.kill()
                        candidate.wait(timeout=10)
                manager.rollback()
                code = report_failure(root, stage, target_version, RuntimeError('Update health check failed'))
                publish_status(root, 'rolled_back', request_id, version=manifest['version'], code=code,
                               retry_at=retry_at(read_json(root / 'failed.json', {})))
            (root / "stop-request.json").unlink(missing_ok=True)
            return 75
        except Exception as error:
            if child.poll() is not None:
                try:
                    manager.rollback()
                finally:
                    report_failure(root, stage, target_version, error)
                return 75
            code = report_failure(root, stage, target_version, error)
            publish_status(root, 'error', request_id, code=code, retry_at=int(time.time()) + 300)
            next_check = time.monotonic() + 300
        finally:
            if staged is not None:
                try:
                    manager.discard_stage(staged)
                except OSError:
                    pass
            flush_reports(root)
    return child.returncode
