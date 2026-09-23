from __future__ import annotations

import ctypes
import logging
import os
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
        kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
        self._wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
        self._wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        self._wts.WTSQuerySessionInformationW.restype = wintypes.BOOL
        self._wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
        user32.GetThreadDesktop.restype = wintypes.HANDLE
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL

    def _session_id(self, pid: int) -> int:
        session = wintypes.DWORD()
        if not self._kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)):
            raise OSError("Cannot determine Windows session")
        return session.value

    def _desktop_name(self, handle) -> str:
        name = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not handle or not self._user32.GetUserObjectInformationW(
                handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            raise OSError("Cannot determine input desktop")
        return name.value

    def is_session_active(self) -> bool:
        session_id = self._session_id(os.getpid())
        if session_id == 0:
            return False
        buffer, size = ctypes.c_void_p(), wintypes.DWORD()
        try:
            if not self._wts.WTSQuerySessionInformationW(None, session_id, 8, ctypes.byref(buffer), ctypes.byref(size)):
                raise OSError("Cannot determine RDP connection state")
            if not buffer.value or size.value < ctypes.sizeof(ctypes.c_int):
                raise OSError("Invalid RDP connection state")
            if ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value != 0:
                return False
        finally:
            if buffer.value:
                self._wts.WTSFreeMemory(buffer)
        # A locked/secure desktop must not inherit the last foreground application.
        desktop = self._user32.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        try:
            current = self._user32.GetThreadDesktop(self._kernel32.GetCurrentThreadId())
            return self._desktop_name(desktop) == self._desktop_name(current)
        finally:
            self._user32.CloseDesktop(desktop)

    def platform_name(self) -> str:
        return "windows"

    def get_active_application(self) -> Optional[ActiveApplication]:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_id = pid.value
        if self._session_id(process_id) != self._session_id(os.getpid()):
            return None
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
            raise OSError("Cannot determine last user input")
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
