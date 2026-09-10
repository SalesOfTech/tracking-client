from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import psutil

from .base import ActiveApplication, PlatformAdapter


class LinuxPlatform(PlatformAdapter):
    def __init__(self):
        if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
            raise RuntimeError("Native application tracking needs an X11 session; this Wayland/headless session is not supported")
        self.xprop = shutil.which("xprop")
        self.xprintidle = shutil.which("xprintidle")
        if not self.xprop or not self.xprintidle:
            raise RuntimeError("Install xprop and xprintidle for Linux application tracking")

    def platform_name(self):
        return "linux"

    def _output(self, arguments):
        return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=2).stdout.strip()

    def get_active_application(self):
        window = re.search(r"0x[0-9a-f]+", self._output([self.xprop, "-root", "_NET_ACTIVE_WINDOW"]), re.I)
        if not window or window.group(0) == "0x0":
            return None
        raw = self._output([self.xprop, "-id", window.group(0), "_NET_WM_PID"])
        match = re.search(r"=\s*(\d+)\s*$", raw)
        if not match:
            return None
        try:
            process = psutil.Process(int(match.group(1)))
            return ActiveApplication(pid=process.pid, process_name=process.name(), executable=process.exe())
        except psutil.Error:
            return None

    def get_idle_duration_ms(self):
        return max(0, int(self._output([self.xprintidle])))

    def ensure_autostart(self, binary_path, enable=True):
        target = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "soft-tracking.desktop"
        if not enable:
            target.unlink(missing_ok=True)
            return
        if any(char in binary_path for char in '\n\r%'):
            raise ValueError("Unsafe autostart path")
        escaped = binary_path.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[Desktop Entry]\nType=Application\nName=SOFT Tracking\nExec="' + escaped + '"\nTerminal=false\n', encoding='utf-8')

    def remove_autostart(self):
        self.ensure_autostart("", False)
