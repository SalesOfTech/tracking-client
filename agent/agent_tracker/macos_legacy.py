"""Retire only the current user's identified Legacy macOS application."""
import os
from pathlib import Path
import plistlib
import subprocess
import uuid
import psutil
from .core.files import atomic_json

LABEL = 'com.soft.agenttracker'
APP = 'SOFT Agent Tracking.app'


def owned_bundle(path, home, uid):
    path = Path(path)
    if path.name != APP or not path.is_dir():
        return False
    try:
        path.resolve().relative_to(home.resolve())
        if path.is_symlink() or path.stat().st_uid != uid:
            return False
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
        executable = info.get('CFBundleExecutable', '')
        return executable == 'SOFT Agent Tracking' and (path / 'Contents/MacOS' / executable).is_file()
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def replace_macos_legacy(root):
    home, uid = Path.home(), os.getuid()
    startup = home / 'Library/LaunchAgents' / (LABEL + '.plist')
    candidates = {home / 'Applications' / APP, home / 'Downloads' / APP}
    startup_bytes = None
    if startup.exists():
        if startup.is_symlink() or startup.stat().st_uid != uid:
            raise ValueError('legacy_migration_denied')
        startup_bytes = startup.read_bytes()
        info = plistlib.loads(startup_bytes)
        args = info.get('ProgramArguments', [])
        if info.get('Label') != LABEL or not args or not isinstance(args[0], str):
            raise ValueError('legacy_migration_unrecognized')
        app = next((p for p in Path(args[0]).parents if p.name == APP), None)
        if app is None or not owned_bundle(app, home, uid):
            raise ValueError('legacy_migration_denied')
        candidates.add(app)
    bundles = {p.resolve() for p in candidates if owned_bundle(p, home, uid)}
    report = {'state': 'not_found', 'disabled': [], 'stopped': [], 'retained': []}
    shared = Path('/Applications') / APP
    if shared.exists():
        report['retained'].append(str(shared))
    if not bundles and startup_bytes is None:
        atomic_json(Path(root) / 'legacy-migration.json', report)
        return report
    report['state'] = 'prepared'
    atomic_json(Path(root) / 'legacy-migration.json', report)
    try:
        if startup_bytes is not None:
            target = 'gui/' + str(uid) + '/' + LABEL
            subprocess.run(['launchctl', 'bootout', target], capture_output=True, timeout=15)
            status = subprocess.run(['launchctl', 'print', target], capture_output=True, timeout=10)
            if status.returncode == 0 or startup.read_bytes() != startup_bytes:
                raise ValueError('legacy_migration_changed')
            saved = Path(root) / ('legacy-startup-' + uuid.uuid4().hex + '.plist')
            startup.rename(saved)
            report['disabled'].append(str(saved))
        executables = {p / 'Contents/MacOS/SOFT Agent Tracking' for p in bundles}
        for process in psutil.process_iter():
            try:
                if process.uids().real != uid or Path(process.exe()).resolve() not in executables:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Unidentified or other-user processes are not migration targets.
                continue
            try:
                process.kill()
                process.wait(timeout=10)
                report['stopped'].append(process.pid)
            except psutil.NoSuchProcess:
                continue
        trash = home / '.Trash'
        trash.mkdir(exist_ok=True, mode=0o700)
        for app in bundles:
            if not owned_bundle(app, home, uid):
                raise ValueError('legacy_migration_changed')
            destination = trash / ('SOFT Agent Tracking-' + uuid.uuid4().hex + '.app')
            app.rename(destination)
            report['disabled'].append(str(destination))
        report['state'] = 'complete'
        return report
    except Exception:
        report['state'] = 'blocked'
        raise
    finally:
        atomic_json(Path(root) / 'legacy-migration.json', report)
