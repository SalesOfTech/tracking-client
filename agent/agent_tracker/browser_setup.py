from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from .native_host import ALLOWED_ORIGIN, HOST_NAME


def host_locations(root):
    if os.name == 'nt':
        return {name:prefix + '\\NativeMessagingHosts\\' + HOST_NAME for name,prefix in {
            'Chrome':r'Software\Google\Chrome', 'Edge':r'Software\Microsoft\Edge', 'Chromium':r'Software\Chromium'}.items()}
    if platform.system() == 'Darwin':
        base = Path.home() / 'Library' / 'Application Support'
        folders = {'Chrome':base/'Google'/'Chrome', 'Edge':base/'Microsoft Edge', 'Chromium':base/'Chromium'}
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
        folders = {'Chrome':base/'google-chrome', 'Edge':base/'microsoft-edge', 'Chromium':base/'chromium'}
    return {name:folder/'NativeMessagingHosts'/(HOST_NAME+'.json') for name,folder in folders.items()}


def register_host(executable: Path, root: Path) -> list:
    """Register our own host only; never edit a browser profile or install policy."""
    executable = Path(executable).resolve(strict=True)
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Native host registration requires a packaged executable, not the Python interpreter")
    manifest = {"name": HOST_NAME, "description": "SOFT Tracking desktop connection", "path": str(executable), "type": "stdio", "allowed_origins": [ALLOWED_ORIGIN]}
    text = json.dumps(manifest, indent=2)
    if os.name == "nt":
        import winreg
        target = Path(root) / (HOST_NAME + ".json")
        target.write_text(text, encoding="utf-8")
        for path in host_locations(root).values():
            # Browsers consult the 32-bit view first, even on 64-bit Windows.
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE | view) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(target))
        return list(host_locations(root))
    for target in host_locations(root).values():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return list(host_locations(root))
