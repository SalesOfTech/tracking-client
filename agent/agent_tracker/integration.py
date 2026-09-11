from __future__ import annotations
import csv
import configparser
import io
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.parsers.expat import ExpatError


def protect_workspace(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        rows = list(csv.reader(io.StringIO(result.stdout)))
        sid = rows[-1][-1] if rows else ""
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise ValueError("Cannot identify the installing Windows account")
        subprocess.run(["icacls", str(root), "/inheritance:r", "/grant:r", "*"+sid+":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        root.chmod(0o700)


def _system():
    return 'windows' if os.name == 'nt' else 'macos' if sys.platform == 'darwin' else 'linux' if sys.platform.startswith('linux') else 'unsupported'


def _startup_path(system):
    if system == 'macos':
        return Path.home() / 'Library' / 'LaunchAgents' / 'com.soft.tracking.v3.plist'
    config = Path(os.environ.get('XDG_CONFIG_HOME', ''))
    if not config.is_absolute():
        config = Path.home() / '.config'
    return config / 'autostart' / 'soft-tracking-v3.desktop'


def _launcher(executable):
    value = str(Path(executable).resolve())
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError('Invalid autostart executable')
    return value


def _desktop_exec(executable):
    # Desktop-entry string escaping happens before Exec argument unquoting.
    quoted = executable.replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
    return '"' + quoted + '" --autostart'


def _startup_bytes(system, executable):
    if system == 'macos':
        return plistlib.dumps({'Label': 'com.soft.tracking.v3',
                              'ProgramArguments': [executable, '--autostart'], 'RunAtLoad': True})
    return ('[Desktop Entry]\nType=Application\nName=SOFT Tracking\nExec=' +
            _desktop_exec(executable) + '\nTerminal=false\n').encode('utf-8')


def _atomic_startup(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def autostart(executable, enabled):
    executable = _launcher(executable)
    if enabled and not Path(executable).is_file():
        raise FileNotFoundError('Autostart launcher is missing')
    system = _system()
    if system == 'windows':
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "SOFT Tracking v3", 0, winreg.REG_SZ, '"' + executable + '" --autostart')
            else:
                try:
                    winreg.DeleteValue(key, "SOFT Tracking v3")
                except FileNotFoundError:
                    pass
    elif system in ('macos', 'linux'):
        path = _startup_path(system)
        if enabled:
            _atomic_startup(path, _startup_bytes(system, executable))
        else:
            path.unlink(missing_ok=True)
    else:
        raise OSError('Unsupported autostart platform')
    if enabled and autostart_status(executable)['registered'] is not True:
        raise OSError('Autostart registration could not be verified')


def autostart_status(executable):
    """Read registration only; OS startup policy/session availability is separate."""
    result = {'registered': False, 'scope': 'user_login', 'effective': 'unknown', 'error': ''}
    try:
        executable = _launcher(executable)
        if not Path(executable).is_file():
            return dict(result, error='launcher_missing')
        system = _system()
        if system == 'windows':
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
                value, kind = winreg.QueryValueEx(key, 'SOFT Tracking v3')
            registered = kind == winreg.REG_SZ and value == '"' + executable + '" --autostart'
        elif system == 'macos':
            with _startup_path(system).open('rb') as stream:
                value = plistlib.load(stream)
            registered = (isinstance(value, dict) and value.get('Label') == 'com.soft.tracking.v3' and
                          value.get('ProgramArguments') == [executable, '--autostart'] and
                          value.get('Program', executable) == executable and
                          value.get('RunAtLoad') is True and not value.get('Disabled', False))
        elif system == 'linux':
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            with _startup_path(system).open(encoding='utf-8') as stream:
                parser.read_file(stream)
            section = parser['Desktop Entry']
            registered = (section.get('Type') == 'Application' and section.get('Exec') == _desktop_exec(executable) and
                          not section.getboolean('Hidden', False) and
                          section.getboolean('X-GNOME-Autostart-enabled', True) and
                          not any(key in section for key in ('OnlyShowIn', 'NotShowIn', 'TryExec', 'AutostartCondition')))
        else:
            return dict(result, error='unsupported')
        return dict(result, registered=registered)
    except FileNotFoundError:
        return result
    except (OSError, ValueError, KeyError, configparser.Error, plistlib.InvalidFileException, ExpatError):
        return dict(result, registered=None, error='unreadable')
