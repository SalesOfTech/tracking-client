from __future__ import annotations
import csv
import io
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path


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


def autostart(executable, enabled):
    executable = str(Path(executable).resolve())
    if os.name == "nt":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                winreg.SetValueEx(key, "SOFT Tracking v3", 0, winreg.REG_SZ, '"' + executable + '" --autostart')
            else:
                try:
                    winreg.DeleteValue(key, "SOFT Tracking v3")
                except FileNotFoundError:
                    pass
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / "com.soft.tracking.v3.plist"
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                plistlib.dump({"Label":"com.soft.tracking.v3", "ProgramArguments":[executable, "--autostart"], "RunAtLoad":True}, stream)
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "soft-tracking-v3.desktop"
        if enabled:
            path.parent.mkdir(parents=True, exist_ok=True)
            quoted = executable.replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$')
            path.write_text('[Desktop Entry]\nType=Application\nName=SOFT Tracking\nExec="'+quoted+'" --autostart\nTerminal=false\n',encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
