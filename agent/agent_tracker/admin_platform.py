"""OS authorization for a cooperative stop, not process tamper protection.

Only fixed, OS-owned programs run elevated. Never elevate the per-user agent,
load its configuration as administrator, or accept a command from the caller.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import subprocess
import sys


TIMEOUT_SECONDS = 180
MAC_SCRIPT = ('do shell script "/usr/bin/true" with administrator privileges '
              'with prompt "SOFT Tracking requests permission to stop tracking."')


def _system():
    return 'windows' if os.name == 'nt' else 'macos' if sys.platform == 'darwin' else 'linux' if sys.platform.startswith('linux') else 'unsupported'


def _windows_directory():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError('System directory unavailable')
    return Path(buffer.value)


def _trusted_unix_tool(filename):
    path = Path(filename)
    resolved = path.resolve(strict=True)
    # Check both paths: a trusted target must not be reached via a writable link.
    for entry in (path, *path.parents, resolved, *resolved.parents):
        info = entry.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise OSError('Untrusted system helper')
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise OSError('System helper unavailable')
    return str(path)


def authorization_status():
    system = _system()
    mechanism = {'windows': 'windows_uac', 'macos': 'macos_administrator',
                 'linux': 'linux_polkit'}.get(system, 'unavailable')
    try:
        if system == 'windows':
            if not (_windows_directory() / 'whoami.exe').is_file():
                raise OSError('System helper unavailable')
        elif system == 'macos':
            _trusted_unix_tool('/usr/bin/osascript')
            _trusted_unix_tool('/usr/bin/true')
        elif system == 'linux':
            _trusted_unix_tool('/usr/bin/pkexec')
            _trusted_unix_tool('/usr/bin/true')
        else:
            raise OSError('Unsupported platform')
    except (OSError, RuntimeError):
        return {'available': False, 'mechanism': mechanism}
    # Availability is not a claim that an interactive authorization agent exists.
    return {'available': True, 'mechanism': mechanism}


def _windows_authorize():
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG),
                    ('hwnd', wintypes.HWND), ('lpVerb', wintypes.LPCWSTR),
                    ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int),
                    ('hInstApp', wintypes.HINSTANCE), ('lpIDList', ctypes.c_void_p),
                    ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE),
                    ('hProcess', wintypes.HANDLE)]

    shell = ctypes.WinDLL('shell32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    directory = _windows_directory()
    request = ShellExecuteInfo()
    request.cbSize = ctypes.sizeof(request)
    request.fMask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS, NOASYNC, FLAG_NO_UI
    request.lpVerb = 'runas'
    request.lpFile = str(directory / 'whoami.exe')
    request.lpDirectory = str(directory)
    request.nShow = 0
    if not shell.ShellExecuteExW(ctypes.byref(request)):
        return 'cancelled' if ctypes.get_last_error() == 1223 else 'denied'
    if not request.hProcess:
        return 'error'
    try:
        waited = kernel.WaitForSingleObject(request.hProcess, TIMEOUT_SECONDS * 1000)
        if waited == 258:
            return 'timed_out'
        if waited != 0:
            return 'error'
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(request.hProcess, ctypes.byref(code)):
            return 'error'
        return 'authorized' if code.value == 0 else 'denied'
    finally:
        kernel.CloseHandle(request.hProcess)


def _unix_authorize(system):
    _trusted_unix_tool('/usr/bin/true')
    if system == 'macos':
        command = [_trusted_unix_tool('/usr/bin/osascript'), '-e', MAC_SCRIPT]
    else:
        command = [_trusted_unix_tool('/usr/bin/pkexec'), '--disable-internal-agent',
                   '--user', 'root', '/usr/bin/true']
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=TIMEOUT_SECONDS, check=False,
                            cwd='/', env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    if result.returncode == 0:
        return 'authorized'
    if system == 'linux' and result.returncode == 126:
        return 'cancelled'
    if system == 'macos' and b'(-128)' in (result.stderr or b''):
        return 'cancelled'
    return 'denied'


def authorize_stop():
    """Block for OS authorization; never stop a process or write a receipt."""
    try:
        if not authorization_status()['available']:
            return 'unavailable'
        return _windows_authorize() if _system() == 'windows' else _unix_authorize(_system())
    except subprocess.TimeoutExpired:
        return 'timed_out'
    except Exception:
        # No exception text, subprocess output or account information crosses IPC.
        return 'error'
