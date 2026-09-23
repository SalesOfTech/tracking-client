"""Read-only Windows shared-install and global restart discovery."""
from pathlib import Path
import time

import psutil


def program_files_roots():
    import winreg
    from win32com.shell import shell, shellcon

    roots = {Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PROGRAM_FILES, 0, 0))}
    # Native registry view also covers a 32-bit migrator on 64-bit Windows.
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows\CurrentVersion',
                            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            for name in ('ProgramFilesDir', 'ProgramFilesDir (x86)'):
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if kind != winreg.REG_SZ or not Path(value).is_absolute():
                    raise ValueError('Invalid Program Files location')
                roots.add(Path(value))
    except FileNotFoundError:
        pass
    return roots


def global_restart_commands():
    """Read known restart mechanisms. Access/inspection failures are not absence."""
    import winreg
    from win32com.client import Dispatch
    from win32com.shell import shell, shellcon

    deadline = time.monotonic() + 30
    count = 0

    def bounded():
        nonlocal count
        count += 1
        if count > 20000 or time.monotonic() > deadline:
            raise ValueError('Global startup inventory incomplete')

    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        for suffix in ('Run', 'RunOnce', 'RunOnceEx', r'Policies\Explorer\Run'):
            key_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion' + '\\' + suffix
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
                # RunOnceEx has nested arbitrary setup commands; fail closed.
                if suffix == 'RunOnceEx' and subkeys:
                    raise ValueError('Global RunOnceEx requires administrator review')
                for index in range(values):
                    bounded()
                    name, value, kind = winreg.EnumValue(key, index)
                    if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                        raise ValueError('Unrecognized global startup entry')
                    yield name + ' ' + value

    for service in psutil.win_service_iter():
        bounded()
        # as_dict also queries optional description/display metadata, which may
        # fail for otherwise inspectable services (QueryServiceConfig2W).
        name, binpath = service.name(), service.binpath()
        start_type, status = service.start_type(), service.status()
        # Stopped demand-start services can be launched by another service/task.
        if start_type != 'disabled' or status != 'stopped':
            yield name + ' ' + binpath

    startup = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_COMMON_STARTUP, 0, 0))
    for path in startup.iterdir():
        bounded()
        if path.is_dir() or path.name.casefold() == 'desktop.ini':
            continue
        if path.suffix.casefold() == '.lnk':
            shortcut = Dispatch('WScript.Shell').CreateShortcut(str(path))
            yield str(shortcut.TargetPath) + ' ' + str(shortcut.Arguments)
        elif path.suffix.casefold() in ('.cmd', '.bat', '.ps1', '.vbs', '.js', '.wsf'):
            # Do not try to interpret arbitrary globally executed code.
            raise ValueError('Global startup script requires administrator review')
        else:
            yield str(path)

    scheduler = Dispatch('Schedule.Service')
    scheduler.Connect()
    folders = [scheduler.GetFolder('\\')]
    while folders:
        bounded()
        folder = folders.pop()
        for task in folder.GetTasks(1):
            bounded()
            if not task.Enabled and task.State != 4:
                continue
            for action in task.Definition.Actions:
                if action.Type == 0:
                    yield str(action.Path) + ' ' + str(action.Arguments)
        folders.extend(folder.GetFolders(0))


def assert_no_global_restart(paths):
    from .legacy_migration import LEGACY_NAME

    paths = [Path(path) for path in paths]
    if not paths:
        return
    names = {path.name.casefold() for path in paths}
    try:
        for command in global_restart_commands():
            # Match basename too: catches variables, short paths and wrapper args.
            if ('softagent' in command.casefold() or LEGACY_NAME.search(command)
                    or any(name in command.casefold() for name in names)):
                raise ValueError('Global Legacy startup requires administrator removal')
    except Exception as error:
        raise ValueError('Cannot exclude global Legacy restart; administrator review required') from error
