"""Replace a per-user Windows Legacy agent without touching other RDP users."""
from __future__ import annotations

import os
from pathlib import Path
import re

import psutil

from .core.files import atomic_json, read_json

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LEGACY_NAME = re.compile(r"(?:SOFT Agent Tracking|soft_agent(?:_windows)?(?:_[0-9][a-z0-9._-]*)?)\.exe", re.I)


def startup_executable(command):
    command = str(command).strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        value = command[1:end] if end > 1 and not command[end + 1:].strip() else ''
    else:
        value = command
    path = Path(value)
    return path if path.is_absolute() and LEGACY_NAME.fullmatch(path.name) else None


def removable(path, profile):
    """Never rename shared binaries, junction targets or anything outside this profile."""
    path, profile = Path(path).absolute(), Path(profile).resolve()
    try:
        path.resolve().relative_to(profile)
    except ValueError:
        return False
    for part in (path, *path.parents):
        if part.exists() and part.samefile(profile):
            break
        if part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
            return False
    return bool(LEGACY_NAME.fullmatch(path.name)) and path.is_file()


def replace_current_user(root):
    if os.name != 'nt':
        return {'state': 'not_applicable'}
    import winreg

    # Use the process owner, not USERNAME, which can be stale after elevation.
    owner = psutil.Process().username().casefold()
    candidates, processes = set(), []
    command = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            command, _ = winreg.QueryValueEx(key, 'SOFTAgent')
    except FileNotFoundError:
        pass
    registered = startup_executable(command) if command else None
    if command and registered is None:
        raise ValueError('legacy_migration_unrecognized')
    if registered:
        candidates.add(registered)
    for process in psutil.process_iter():
        try:
            name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not LEGACY_NAME.fullmatch(name):
            continue
        try:
            if process.username().casefold() != owner:
                continue
            executable = Path(process.exe())
            if not LEGACY_NAME.fullmatch(executable.name):
                continue
            candidates.add(executable)
            processes.append(process)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            # Only an identified Legacy process with unknown ownership blocks replacement.
            raise ValueError('legacy_migration_denied') from error
    if not candidates:
        return read_json(Path(root) / 'legacy-migration.json', {'state': 'not_found'})
    report = {'state': 'prepared', 'startup': command, 'owner': owner,
              'files': [str(path) for path in sorted(candidates)], 'stopped': [], 'retained': [], 'disabled': []}
    atomic_json(Path(root) / 'legacy-migration.json', report)
    if registered:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            if winreg.QueryValueEx(key, 'SOFTAgent')[0] != command:
                raise ValueError('legacy_migration_changed')
            winreg.DeleteValue(key, 'SOFTAgent')
    try:
        for process in processes:
            try:
                # psutil retains creation time and refuses to kill a recycled PID.
                if process.username().casefold() != owner or Path(process.exe()) not in candidates:
                    raise ValueError('legacy_migration_changed')
                process.kill()
                process.wait(timeout=10)
                report['stopped'].append(process.pid)
            except psutil.NoSuchProcess:
                pass
        for path in candidates:
            if removable(path, Path.home()):
                disabled = path.with_name(path.name + '.legacy-disabled')
                if disabled.exists():
                    # Do not overwrite a previous rollback copy.
                    raise ValueError('legacy_migration_changed')
                path.rename(disabled)
                report['disabled'].append(str(disabled))
            else:
                report['retained'].append(str(path))
        report['state'] = 'complete'
        return report
    except (OSError, psutil.Error) as error:
        report['state'] = 'blocked'
        raise ValueError('legacy_migration_denied') from error
    except ValueError:
        report['state'] = 'blocked'
        raise
    finally:
        atomic_json(Path(root) / 'legacy-migration.json', report)
