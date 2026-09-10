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
    if not re.fullmatch('[a-f0-9]{32}', profile) or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version):
        raise ValueError('Invalid browser receipt')
    if family not in ('Chrome', 'Edge', 'Opera', 'Chromium'):
        raise ValueError('Invalid browser family')
    error = value.get('error', '')
    if error not in ('', 'storage_error'):
        raise ValueError('Invalid browser health')
    # One row per extension profile, shared safely with the running desktop process.
    client.state.set('browser:' + profile, dict(version=version, family=family, error=error, last_seen=int(time.time())))


def connections(client, now=None):
    now = time.time() if now is None else now
    with client.state.lock:
        rows = client.state.db.execute("SELECT name FROM state WHERE name LIKE 'browser:%' ORDER BY name LIMIT 100").fetchall()
    result = []
    for (name,) in rows:
        value = client.state.get(name, {})
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
    from .native_host import ALLOWED_ORIGIN, HOST_NAME
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
        valid = manifest.get('name') == HOST_NAME and ALLOWED_ORIGIN in manifest.get('allowed_origins', [])
        valid = valid and Path(manifest.get('path', '')).resolve() == expected.resolve() and expected.is_file()
        results.append((name, bool(valid)))
    return results
