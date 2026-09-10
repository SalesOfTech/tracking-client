from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional

import psutil

from .base import ActiveApplication, PlatformAdapter


LOG = logging.getLogger(__name__)


class WindowsPlatform(PlatformAdapter):
    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = user32
        self._kernel32 = kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        kernel32.GetTickCount.restype = wintypes.DWORD

    def platform_name(self) -> str:
        return "windows"

    def get_active_application(self) -> Optional[ActiveApplication]:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = pid.value
        exe_path = None
        exe_name = None
        try:
            process = psutil.Process(process_id)
            exe_name = process.name()
            exe_path = process.exe()
        except psutil.Error:
            return None

        length = self._user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(hwnd, buffer, length + 1)
        return ActiveApplication(
            pid=process_id,
            process_name=exe_name,
            title=buffer.value,
            executable=exe_path,
        )

    def get_idle_duration_ms(self) -> int:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        plii = LASTINPUTINFO()
        plii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not self._user32.GetLastInputInfo(ctypes.byref(plii)):
            return 0
        tick_count = self._kernel32.GetTickCount()
        elapsed = (tick_count - plii.dwTime) & 0xffffffff
        return int(elapsed)

    def ensure_autostart(self, binary_path: str, enable: bool = True) -> None:
        import winreg

        run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_ALL_ACCESS) as key:
            if enable:
                winreg.SetValueEx(key, "SOFTAgent", 0, winreg.REG_SZ, '"' + binary_path + '"')
                LOG.info("Autostart enabled for %s", binary_path)
            else:
                try:
                    winreg.DeleteValue(key, "SOFTAgent")
                except FileNotFoundError:
                    pass

    def remove_autostart(self) -> None:
        self.ensure_autostart("", enable=False)
