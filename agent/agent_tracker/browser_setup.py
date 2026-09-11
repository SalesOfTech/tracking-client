from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .native_host import HOST_NAME, host_manifest


BROWSER_FAMILIES = ('Chrome', 'Edge', 'Yandex', 'Opera', 'Brave', 'Vivaldi', 'Chromium', 'Firefox')
EXTENSION_PAGES = dict(zip(BROWSER_FAMILIES, (
    'chrome://extensions', 'edge://extensions', 'browser://extensions', 'opera://extensions',
    'brave://extensions', 'vivaldi://extensions', 'chrome://extensions', 'about:addons')))
_WINDOWS_PATHS = {
    'Chrome': ('Google/Chrome/Application/chrome.exe',),
    'Edge': ('Microsoft/Edge/Application/msedge.exe',),
    'Yandex': ('Yandex/YandexBrowser/Application/browser.exe',),
    'Opera': ('Programs/Opera/launcher.exe', 'Opera/launcher.exe'),
    'Brave': ('BraveSoftware/Brave-Browser/Application/brave.exe',),
    'Vivaldi': ('Vivaldi/Application/vivaldi.exe',),
    'Chromium': ('Chromium/Application/chrome.exe',),
    'Firefox': ('Mozilla Firefox/firefox.exe',),
}
_MAC_APPS = dict(zip(BROWSER_FAMILIES, ('Google Chrome.app', 'Microsoft Edge.app',
    'Yandex.app', 'Opera.app', 'Brave Browser.app', 'Vivaldi.app', 'Chromium.app', 'Firefox.app')))
_LINUX_COMMANDS = {
    'Chrome': ('google-chrome', 'google-chrome-stable'), 'Edge': ('microsoft-edge', 'microsoft-edge-stable'),
    'Yandex': ('yandex-browser', 'yandex-browser-stable'), 'Opera': ('opera',),
    'Brave': ('brave-browser', 'brave-browser-stable'), 'Vivaldi': ('vivaldi', 'vivaldi-stable'),
    'Chromium': ('chromium', 'chromium-browser'), 'Firefox': ('firefox',),
}


def resolve_browser(family):
    """Read installed application locations, never profiles or browser preferences."""
    if not isinstance(family, str) or family not in BROWSER_FAMILIES:
        raise ValueError('Unsupported browser family')
    if os.name == 'nt':
        for variable in ('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'):
            base = os.environ.get(variable)
            if not base:
                continue
            for relative in _WINDOWS_PATHS[family]:
                candidate = Path(base) / relative
                if candidate.is_file():
                    return candidate.resolve()
    elif platform.system() == 'Darwin':
        for base in (Path('/Applications'), Path.home() / 'Applications'):
            candidate = base / _MAC_APPS[family]
            if candidate.is_dir():
                return candidate.resolve()
    else:
        for command in _LINUX_COMMANDS[family]:
            found = shutil.which(command)
            if found and Path(found).is_file():
                return Path(found).resolve()
    return None


def discover_browsers():
    """Local guide metadata. Detection is not installation or runtime certification."""
    return [dict(family=family, installed=resolve_browser(family) is not None,
                 extension_page=EXTENSION_PAGES[family], engine='gecko' if family == 'Firefox' else 'chromium',
                 support_level='signed_package_required' if family == 'Firefox' else
                 'native_host_verification_required' if family in ('Yandex', 'Opera', 'Vivaldi') else 'manual_setup',
                 signed_package_required=family == 'Firefox') for family in BROWSER_FAMILIES]


def open_extensions_page(family):
    """Launch exactly the requested installed family with its fixed internal URL."""
    executable = resolve_browser(family)
    if executable is None:
        raise ValueError('browser_not_found')
    url = EXTENSION_PAGES[family]
    command = ['/usr/bin/open', '-a', str(executable), url] if platform.system() == 'Darwin' else [str(executable), url]
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    except OSError:
        raise ValueError('browser_open_failed') from None
    return {'browser': family, 'extension_page': url, 'opened': True}


def host_locations(root):
    if os.name == 'nt':
        return {name:prefix + '\\NativeMessagingHosts\\' + HOST_NAME for name,prefix in {
            'Chrome':r'Software\Google\Chrome', 'Edge':r'Software\Microsoft\Edge',
            'Yandex':r'Software\Yandex\YandexBrowser', 'Opera':r'Software\Google\Chrome',
            'Brave':r'Software\BraveSoftware\Brave-Browser', 'Vivaldi':r'Software\Vivaldi',
            'Chromium':r'Software\Chromium', 'Firefox':r'Software\Mozilla'}.items()}
    if platform.system() == 'Darwin':
        base = Path.home() / 'Library' / 'Application Support'
        folders = {'Chrome':base/'Google'/'Chrome', 'Edge':base/'Microsoft Edge',
                   'Yandex':base/'Yandex'/'YandexBrowser', 'Opera':base/'Google'/'Chrome',
                   'Brave':base/'Google'/'Chrome', 'Vivaldi':base/'Vivaldi', 'Chromium':base/'Chromium'}
        firefox = base/'Mozilla'/'NativeMessagingHosts'/(HOST_NAME+'.json')
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
        folders = {'Chrome':base/'google-chrome', 'Edge':base/'microsoft-edge',
                   'Yandex':base/'yandex-browser', 'Brave':base/'BraveSoftware'/'Brave-Browser',
                   'Vivaldi':base/'vivaldi', 'Chromium':base/'chromium'}
        # Opera documents a system-only Linux location; do not silently elevate/write it.
        firefox = Path.home()/'.mozilla'/'native-messaging-hosts'/(HOST_NAME+'.json')
    return dict({name:folder/'NativeMessagingHosts'/(HOST_NAME+'.json') for name,folder in folders.items()}, Firefox=firefox)


def register_host(executable: Path, root: Path) -> list:
    """Register our own host only; never edit a browser profile or install policy."""
    executable = Path(executable).resolve(strict=True)
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Native host registration requires a packaged executable, not the Python interpreter")
    locations = host_locations(root)
    manifests = {engine:json.dumps(host_manifest(executable, engine), indent=2) for engine in ('chromium', 'gecko')}
    if os.name == "nt":
        import winreg
        targets = {engine:Path(root) / (HOST_NAME + ('.firefox' if engine == 'gecko' else '') + '.json') for engine in manifests}
        for engine, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(manifests[engine], encoding='utf-8')
        for name, path in locations.items():
            target = targets['gecko' if name == 'Firefox' else 'chromium']
            # Browsers consult the 32-bit view first, even on 64-bit Windows.
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE | view) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(target))
        return list(locations)
    for name, target in locations.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(manifests['gecko' if name == 'Firefox' else 'chromium'], encoding="utf-8")
    return list(locations)
