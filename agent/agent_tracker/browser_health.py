"""Local extension receipts contain connection metadata, never browsing history."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .core.files import read_json

FRESH_SECONDS = 150


def receive(client, value):
    if not isinstance(value, dict):
        raise ValueError('Invalid browser receipt')
    profile = value.get('profile', '')
    version = value.get('version', '')
    family = value.get('family', '')
    if not isinstance(profile, str) or not isinstance(version, str) or not re.fullmatch('[a-f0-9]{32}', profile) or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version):
        raise ValueError('Invalid browser receipt')
    from .browser_setup import BROWSER_FAMILIES
    if not isinstance(family, str) or family not in BROWSER_FAMILIES:
        raise ValueError('Invalid browser family')
    epoch_protocol = value.get('epoch_protocol', 0)
    if type(epoch_protocol) is not int or epoch_protocol not in (0, 1):
        raise ValueError('Invalid browser epoch protocol')
    error = value.get('error', '')
    if error not in ('', 'storage_error'):
        raise ValueError('Invalid browser health')
    # One row per extension profile, shared safely with the running desktop process.
    client.state.set('browser:' + profile, dict(version=version, family=family, error=error,
                                              epoch_protocol=epoch_protocol, last_seen=int(time.time())))


def employee_switch_ready(client) -> bool:
    """Only actual, recent epoch-capable extension handshakes permit switching."""
    return all(type(row.get('epoch_protocol')) is int and row['epoch_protocol'] == 1
               and not row.get('error') for row in connections(client) if row['connected'])


def connections(client, now=None):
    now = time.time() if now is None else now
    with client.state.lock:
        rows = client.state.db.execute("SELECT name FROM state WHERE name LIKE 'browser:%' ORDER BY name").fetchall()
    result = []
    for (name,) in rows:
        value = client.state.get(name, {})
        if not isinstance(value, dict) or type(value.get('last_seen')) not in (int, float):
            value = dict(last_seen=now, epoch_protocol=0, error='storage_error')
        age = max(0, int(now - value.get('last_seen', 0)))
        result.append(dict(value, profile=name[8:], age=age, connected=age <= FRESH_SECONDS))
    return sorted(result, key=lambda row: row['last_seen'], reverse=True)


def open_desktop():
    install = os.environ.get('SOFT_TRACKING_INSTALL')
    if not install or not getattr(sys, 'frozen', False):
        raise ValueError('Packaged application required')
    launcher = Path(install) / ('SoftTracking.exe' if os.name == 'nt' else 'soft-tracking')
    if not launcher.is_file():
        raise ValueError('Launcher missing')
    subprocess.Popen([str(launcher)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def registration(install, root):
    from .native_host import host_manifest
    from .browser_setup import host_locations
    expected = Path(install) / ('SoftTrackingHost.exe' if os.name == 'nt' else 'soft-tracking-host')
    results = []
    for name, path in host_locations(root).items():
        if os.name == 'nt':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    path = winreg.QueryValueEx(key, '')[0]
            except OSError:
                results.append((name, False))
                continue
        manifest = read_json(Path(path), {})
        wanted = host_manifest(expected, 'gecko' if name == 'Firefox' else 'chromium')
        valid = all(manifest.get(field) == wanted.get(field) for field in
                    ('name', 'type', 'allowed_origins', 'allowed_extensions'))
        valid = valid and Path(manifest.get('path', '')).resolve() == expected.resolve() and expected.is_file()
        results.append((name, bool(valid)))
    return results
