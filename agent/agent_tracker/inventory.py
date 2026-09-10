"""Installed application metadata from OS application catalogs, not user documents."""
import configparser
import os
import plistlib
import re
import shlex
import sys
from pathlib import Path


def clean(rows):
    result = {}
    for row in rows:
        name = str(row.get('name', '')).strip()[:160]
        executable = str(row.get('executable', '')).replace('\\', '/').rsplit('/', 1)[-1].strip()[:160]
        if not name or any(ord(c)<32 for c in name+executable):
            continue
        # Only names leave the device: no install paths, usernames or command arguments.
        if executable and not re.fullmatch(r'[\w .+()@-]+', executable, re.UNICODE):
            executable = ''
        key = (name.casefold(), executable.casefold())
        result[key] = dict(name=name, executable=executable)
    return sorted(result.values(), key=lambda row:row['name'].casefold())[:1000]


def linux_apps(paths):
    rows = []
    for folder in paths:
        for file in Path(folder).glob('*.desktop'):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                parser.read(file, encoding='utf-8')
                entry = parser['Desktop Entry']
                if entry.get('Type') != 'Application' or entry.get('Hidden', 'false') == 'true':
                    continue
                command = shlex.split(entry.get('Exec', ''))
                executable = command[0] if command and Path(command[0]).name not in ('env', 'sh', 'bash', 'flatpak', 'snap') else ''
                rows.append(dict(name=entry.get('Name', ''), executable=executable))
            except (OSError, ValueError, KeyError, configparser.Error):
                continue
    return rows


def mac_apps(paths):
    rows = []
    for folder in paths:
        for app in Path(folder).glob('*.app'):
            try:
                with (app/'Contents'/'Info.plist').open('rb') as stream:
                    info = plistlib.load(stream)
                rows.append(dict(name=info.get('CFBundleDisplayName') or info.get('CFBundleName') or app.stem,
                                 executable=info.get('CFBundleExecutable', '')))
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
    return rows


def windows_apps():
    import winreg
    rows = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ|view) as catalog:
                    for index in range(min(winreg.QueryInfoKey(catalog)[0], 5000)):
                        try:
                            with winreg.OpenKey(catalog, winreg.EnumKey(catalog, index)) as key:
                                name = winreg.QueryValueEx(key, 'DisplayName')[0]
                                try:
                                    icon = str(winreg.QueryValueEx(key, 'DisplayIcon')[0]).strip().strip('"')
                                except OSError:
                                    icon = ''
                                match = re.match(r'^"?(.+?\.exe)(?:"?(?:,\s*-?\d+)?)?$', icon, re.I)
                                rows.append(dict(name=name, executable=match.group(1) if match else ''))
                        except OSError:
                            continue
            except OSError:
                continue
    return rows


def installed_apps():
    if os.name == 'nt':
        return clean(windows_apps())
    if sys.platform == 'darwin':
        return clean(mac_apps(['/Applications', '/System/Applications', Path.home()/'Applications']))
    paths = [Path(folder)/'applications' for folder in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':')]
    paths.append(Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'applications')
    return clean(linux_apps(paths))
