"""Current-user Windows removal. Never infer server completion from a request."""
from __future__ import annotations

import base64
import ctypes
import os
from pathlib import Path
import shutil
import stat
import subprocess
import threading

from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .i18n import client_language, translate

UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\SOFT Tracking v3'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
MARKER = 'uninstall-requested.json'


def language(root):
    import json
    import sqlite3
    from contextlib import closing
    saved = ''
    try:
        with closing(sqlite3.connect((Path(root).parent / 'state.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            row = db.execute("SELECT value FROM state WHERE name='language'").fetchone()
            saved = json.loads(row[0]) if row else ''
    except (OSError, ValueError, sqlite3.Error):
        pass
    return client_language(saved, read_json(Path(root) / 'enrollment.json', {}))


def known_folder(csidl):
    # Shell account folders, deliberately not SOFT_TRACKING_INSTALL or env overrides.
    buffer = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer):
        raise OSError('Cannot locate current-user Windows folder')
    return Path(buffer.value)


def reject_reparse(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Refusing a reparse-point uninstall path')


def owned_root(root):
    if os.name != 'nt':
        raise OSError('Windows per-user uninstall only')
    expected = known_folder(28) / 'SOFT' / 'TrackingV3' / 'install'
    root = Path(root).absolute()
    reject_reparse(root)
    reject_reparse(expected)
    if root != expected or root.resolve() != expected.resolve():
        raise ValueError('Not the current-user SOFT Tracking installation')
    return root


def validate_tree(root):
    reject_reparse(root)
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            reject_reparse(Path(folder) / name)


def register_uninstaller(root):
    if os.name != 'nt':
        return
    import winreg
    from .bootstrap import app_path
    root = owned_root(root)
    version = read_json(root / 'current.json', {})['version']
    source = app_path(root).parent.parent / 'launcher' / 'SoftTracking.exe'
    launcher = root / 'SoftTrackingUninstall.exe'
    temporary = root / 'SoftTrackingUninstall.exe.tmp'
    reject_reparse(launcher)
    reject_reparse(temporary)
    shutil.copy2(source, temporary)
    os.replace(temporary, launcher)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                           winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY) as key:
        for name, value in {'DisplayName': 'SOFT Tracking', 'Publisher': 'SOFT',
                            'DisplayVersion': version, 'InstallLocation': str(root),
                            'DisplayIcon': str(launcher),
                            'UninstallString': subprocess.list2cmdline([str(launcher), '--uninstall'])}.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ('NoModify', 'NoRepair'):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
    atomic_json(root / 'uninstaller-status.json', {'state': 'registered', 'version': version})


def remove_owned_registrations(root):
    import winreg
    from .browser_setup import host_locations
    from .native_host import HOST_NAME
    root = owned_root(root)
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE | view) as key:
                value, kind = winreg.QueryValueEx(key, 'SOFT Tracking v3')
                if kind == winreg.REG_SZ and value == '"' + str(root / 'SoftTracking.exe') + '" --autostart':
                    winreg.DeleteValue(key, 'SOFT Tracking v3')
        except FileNotFoundError:
            pass
        for browser, location in host_locations(root.parent).items():
            manifest = root.parent / (HOST_NAME + ('.firefox' if browser == 'Firefox' else '') + '.json')
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, location, 0, winreg.KEY_READ | view) as key:
                    value, kind = winreg.QueryValueEx(key, '')
                if kind == winreg.REG_SZ and value == str(manifest):
                    winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, location, view, 0)
            except FileNotFoundError:
                pass
    shortcut = known_folder(26) / 'Microsoft/Windows/Start Menu/Programs/SOFT Tracking.lnk'
    reject_reparse(shortcut)
    if shortcut.exists():
        from win32com.client import Dispatch
        target = Dispatch('WScript.Shell').CreateShortCut(str(shortcut)).Targetpath
        if Path(target) == root / 'SoftTracking.exe':
            shortcut.unlink()


def notify_requested(root, timeout=5):
    """Best effort only: offline/failed delivery must not prevent local removal."""
    def send():
        client = None
        try:
            from .core.client import Client
            from .core.lifecycle import record, flush
            if not (root.parent / 'state.sqlite3').is_file():
                return
            client = Client(root.parent)
            client.http.timeout = 2
            try:
                client.flush()
            except Exception:
                pass
            record(client, 'uninstall_requested')
            flush(client, active_only=True)
        except Exception:
            pass
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    worker = threading.Thread(target=send, daemon=True)
    worker.start()
    worker.join(timeout)


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def helper_script(root, parent_pid):
    root = owned_root(root)
    locale = language(root)
    # No shell command strings, wildcard deletion, process-name kills, or elevation.
    return r'''
$ErrorActionPreference = 'Stop'
$workspace = __WORKSPACE__
$install = __INSTALL__
$parentId = __PARENT_PID__
$retryLauncher = Join-Path $install 'SoftTrackingUninstall.exe'
$marker = Join-Path $install 'uninstall-requested.json'
function Assert-Safe([string]$path) {
    $full = [IO.Path]::GetFullPath($path)
    if ($full -ne $workspace -and -not $full.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Outside owned workspace'
    }
    for ($p = $full; $p; $p = [IO.Path]::GetDirectoryName($p)) {
        if (Test-Path -LiteralPath $p) {
            $item = Get-Item -LiteralPath $p -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point refused' }
        }
    }
}
function Remove-Owned([string]$path) {
    Assert-Safe $path
    if (-not (Test-Path -LiteralPath $path)) { return }
    if ($path -eq $retryLauncher -or $path -eq $marker) { return }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $path -Force)) { Remove-Owned $child.FullName }
    }
    Assert-Safe $path
    if ($path -eq $workspace -or $path -eq $install) { return }
    Remove-Item -LiteralPath $path -Force
}
try {
    $expected = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'SOFT\TrackingV3'
    if ($workspace -ne $expected -or $install -ne (Join-Path $expected 'install')) { throw 'Wrong user path' }
    Assert-Safe $workspace
    $parent = Get-Process -Id $parentId -ErrorAction SilentlyContinue
    if ($parent -and -not $parent.WaitForExit(90000)) { throw 'Uninstaller still running' }
    $done = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Remove-Owned $workspace
            Assert-Safe $marker
            if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force }
            Assert-Safe $retryLauncher
            if (Test-Path -LiteralPath $retryLauncher) { Remove-Item -LiteralPath $retryLauncher -Force }
            Assert-Safe $install
            if (Test-Path -LiteralPath $install) { Remove-Item -LiteralPath $install -Force }
            Assert-Safe $workspace
            if (Test-Path -LiteralPath $workspace) { Remove-Item -LiteralPath $workspace -Force }
            $done = $true
            break
        }
        catch { Start-Sleep -Seconds 2 }
    }
    if (-not $done) { throw 'Files still in use. Close SOFT Tracking and retry uninstall.' }
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryView]::Registry64)
    $key = $base.OpenSubKey(__UNINSTALL_KEY__)
    if ($key) {
        $owned = $key.GetValue('InstallLocation') -eq $install
        $key.Close()
        if ($owned) { $base.DeleteSubKey(__UNINSTALL_KEY__, $false) }
    }
    $base.Close()
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(__FAILURE_TEXT__, __TITLE__)
    exit 1
}
'''.replace('__WORKSPACE__', ps_literal(root.parent)).replace('__INSTALL__', ps_literal(root)).replace(
        '__PARENT_PID__', str(int(parent_pid))).replace('__UNINSTALL_KEY__', ps_literal(UNINSTALL_KEY)).replace(
        '__FAILURE_TEXT__', ps_literal(translate(locale, 'uninstall_incomplete'))).replace(
        '__TITLE__', ps_literal(translate(locale, 'uninstall_title')))


def start_helper(root):
    script = helper_script(root, os.getpid())
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    # CSIDL_SYSTEM avoids PATH/env command substitution.
    powershell = known_folder(37) / 'WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                             '-EncodedCommand', encoded], cwd=str(known_folder(28)),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def confirm_removal(root):
    locale = language(root)
    return ctypes.windll.user32.MessageBoxW(None,
        translate(locale, 'uninstall_confirm') + '\n\n' + translate(locale, 'uninstall_flush_warning'),
        translate(locale, 'uninstall_title'),
        0x4 | 0x30 | 0x100) == 6


def uninstall(root, confirm=None, lifecycle_hook=None):
    """Return 0 when cleanup was scheduled, NOT when deletion was completed.

    lifecycle_hook(root) may replace bounded best-effort lifecycle delivery.
    It must describe uninstall_requested only, never confirmed removal.
    """
    from .installer import stopped_supervisor
    root = owned_root(root)
    if not (confirm() if confirm else confirm_removal(root)):
        return 0
    validate_tree(root.parent)
    with SingleInstance(root / 'install.lock'):
        atomic_json(root / MARKER, {'state': 'uninstall_requested'})
        try:
            with stopped_supervisor(root):
                try:
                    (lifecycle_hook or notify_requested)(root)
                except Exception:
                    pass
                remove_owned_registrations(root)
                start_helper(root)
        except Exception:
            (root / MARKER).unlink(missing_ok=True)
            raise
    return 0


def main(root):
    try:
        return uninstall(root)
    except Exception:
        if os.name == 'nt':
            locale = language(root)
            ctypes.windll.user32.MessageBoxW(None,
                translate(locale, 'uninstall_failed'), translate(locale, 'uninstall_title'), 0x10)
        return 1
