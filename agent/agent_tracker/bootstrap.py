from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from .core.files import atomic_json, inside, read_json
from .core.instance import SingleInstance


def app_path(root, native=False):
    import re
    state = read_json(root / "current.json", {})
    version = state.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", version):
        raise ValueError("Invalid installation pointer")
    name = ("SoftTrackingHostApp.exe" if native else "SoftTrackingApp.exe") if os.name == "nt" else ("soft-tracking-host-app" if native else "soft-tracking-app")
    return inside(root, root / "versions" / version / "app" / name)


def main(native=False, root=None, args=None):
    root = Path(root or Path(sys.executable).resolve().parent)
    args = list(sys.argv[1:] if args is None else args)
    if not native and '--uninstall' in args:
        if args != ['--uninstall']:
            return 2
        from .uninstaller import main as uninstall_main
        return uninstall_main(root)
    if (root / 'uninstall-requested.json').exists():
        return 1
    if native:
        from .native_host import ALLOWED_ORIGIN
        if not args or args[0] != ALLOWED_ORIGIN:
            return 2
        env = dict(os.environ, SOFT_TRACKING_INSTALL=str(root))
        return subprocess.call([str(app_path(root, True))] + args, env=env,
                               stdin=sys.stdin.buffer, stdout=sys.stdout.buffer, stderr=sys.stderr,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    try:
        with SingleInstance(root / "supervisor.lock"):
            if (root / 'uninstall-requested.json').exists():
                return 1
            for _ in range(5):
                if (root / 'uninstall-requested.json').exists():
                    return 1
                result = subprocess.call([str(app_path(root)), "--supervisor", "--installed-root", str(root)] + args)
                if result != 75:
                    return result
            return 1
    except RuntimeError:
        if '--autostart' not in args:
            atomic_json(root / "show-window.json", {"show": True})
        return 0
