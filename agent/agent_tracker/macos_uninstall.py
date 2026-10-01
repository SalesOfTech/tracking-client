"""Deletion confined to the real account home, using no-follow directory handles."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import sys
from xml.parsers.expat import ExpatError


LABEL = 'com.soft.tracking.v3'
WRAPPER = Path('Applications/SOFT Tracking.app')
WORKSPACE = Path('Library/Application Support/SOFT/TrackingV3')
USER_FLAGS = getattr(stat, 'UF_IMMUTABLE', 2) | getattr(stat, 'UF_APPEND', 4)
SYSTEM_FLAGS = getattr(stat, 'SF_IMMUTABLE', 0x20000) | getattr(stat, 'SF_APPEND', 0x40000)


def current_user():
    import pwd
    uid = os.getuid()
    if uid == 0 or os.geteuid() != uid:
        raise ValueError('Per-user removal requires an ordinary account')
    # HOME and SOFT_TRACKING_INSTALL are not authorities for destructive operations.
    return Path(pwd.getpwuid(uid).pw_dir), uid


def _check(info, uid, device=None):
    if info.st_uid != uid or (device is not None and info.st_dev != device):
        raise ValueError('Foreign owner or mounted filesystem')
    if not any(check(info.st_mode) for check in (stat.S_ISDIR, stat.S_ISREG, stat.S_ISLNK)):
        raise ValueError('Special file refused')
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError('Hard-linked file refused')
    if getattr(info, 'st_flags', 0) & SYSTEM_FLAGS:
        raise ValueError('System-protected file refused')


@contextmanager
def _directory(home, path, uid):
    home, path = Path(home), Path(path)
    if not home.is_absolute() or not path.is_absolute() or '..' in path.parts:
        raise ValueError('Noncanonical user path')
    path.relative_to(home)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            current /= part
            if current == home or home in current.parents:
                _check(os.fstat(descriptor), uid)
        yield descriptor
    finally:
        os.close(descriptor)


def owned_root(root):
    home, uid = current_user()
    root = Path(root)
    if root != home / WORKSPACE / 'install':
        raise ValueError('Not the current-user installation')
    with _directory(home, root, uid):
        pass
    return root


def _identity(info):
    return info.st_dev, info.st_ino


def _unchanged(parent, name, before):
    now = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _identity(now) != _identity(before):
        raise ValueError('Removal target changed')


def _repair(descriptor, info):
    # Only the owner's mutable flags and directory mode, never a link target.
    if getattr(info, 'st_flags', 0) & USER_FLAGS:
        os.fchflags(descriptor, info.st_flags & ~USER_FLAGS)
    if stat.S_ISDIR(info.st_mode) and info.st_mode & 0o700 != 0o700:
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode) | 0o700)


def _visit(parent, name, uid, device, remove=False, payload_links=False, parts=(), expected=None):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if expected is not None and _identity(info) != expected:
        raise ValueError('Registration changed before removal')
    _check(info, uid, device)
    if stat.S_ISLNK(info.st_mode):
        # Framework aliases, including dangling links, are entries, not subtrees.
        if not (payload_links and len(parts) >= 5 and parts[:2] == ('install', 'versions') and parts[3] == 'app'):
            raise ValueError('Link outside versioned application payload')
        if getattr(info, 'st_flags', 0) & USER_FLAGS:
            raise ValueError('Protected symlink refused')
        if remove:
            _unchanged(parent, name, info)
            os.unlink(name, dir_fd=parent)
        return
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(info.st_mode):
        flags |= os.O_DIRECTORY
    descriptor = os.open(name, flags, dir_fd=parent)
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(info):
            raise ValueError('Removal target changed')
        _check(opened, uid, device)
        if remove:
            _repair(descriptor, opened)
        if stat.S_ISDIR(info.st_mode):
            # Leave the tombstone until the stable launchers and payload are gone.
            for child in sorted(os.listdir(descriptor), key=lambda value: value == 'uninstall-requested.json'):
                _visit(descriptor, child, uid, device, remove, payload_links, parts + (child,))
        if remove:
            _unchanged(parent, name, info)
            (os.rmdir if stat.S_ISDIR(info.st_mode) else os.unlink)(name, dir_fd=parent)
    finally:
        os.close(descriptor)


def validate_tree(path):
    home, uid = current_user()
    path = Path(path)
    with _directory(home, path.parent, uid) as parent:
        _visit(parent, path.name, uid, os.fstat(parent).st_dev, payload_links=path == home / WORKSPACE)


def _remove_tree(path, expected=None):
    home, uid = current_user()
    with _directory(home, path.parent, uid) as parent:
        # Parent is outside the deletion tree: never repair or chmod it.
        _visit(parent, path.name, uid, os.fstat(parent).st_dev, remove=True,
               payload_links=path == home / WORKSPACE, expected=expected)


def _read(path, identity=False):
    home, uid = current_user()
    with _directory(home, path.parent, uid) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            _check(info, uid, os.fstat(parent).st_dev)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise ValueError('Invalid registration file')
            data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError('Invalid registration file')
            return (data, _identity(info)) if identity else data


def wrapper_script(root):
    return '#!/bin/sh\nexec ' + shlex.quote(str(root / 'soft-tracking')) + ' "$@"\n'


def owned_wrapper(root):
    home, uid = current_user()
    wrapper = home / WRAPPER
    try:
        with _directory(home, wrapper, uid) as folder:
            if set(os.listdir(folder)) != {'Contents'}:
                return None
        with _directory(home, wrapper / 'Contents', uid) as folder:
            if set(os.listdir(folder)) != {'MacOS', 'Info.plist'}:
                return None
        with _directory(home, wrapper / 'Contents/MacOS', uid) as folder:
            if set(os.listdir(folder)) != {'tracking'}:
                return None
        metadata = plistlib.loads(_read(wrapper / 'Contents/Info.plist'))
        if (metadata.get('CFBundleIdentifier') != 'com.soft.tracking' or
                metadata.get('CFBundleExecutable') != 'tracking' or
                _read(wrapper / 'Contents/MacOS/tracking').decode('utf-8') != wrapper_script(root)):
            return None
        validate_tree(wrapper)
        return wrapper
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException, ExpatError):
        return None


def registration_paths():
    home, _ = current_user()
    base = home / 'Library/Application Support'
    # The same current-user locations written by browser_setup.host_locations.
    folders = ('Google/Chrome', 'Microsoft Edge', 'Yandex/YandexBrowser',
               'Vivaldi', 'Chromium', 'Mozilla')
    return [base / folder / 'NativeMessagingHosts/com.soft.tracking.json' for folder in folders]


def owned_registration(path, root, snapshot=False):
    try:
        data, identity = _read(path, identity=True)
        if path.suffix == '.plist':
            value = plistlib.loads(data)
            launcher = str(root / 'soft-tracking')
            owned = (value.get('Label') == LABEL and value.get('ProgramArguments') == [launcher, '--autostart']
                     and value.get('Program', launcher) == launcher)
        else:
            from .native_host import host_manifest
            engine = 'gecko' if path.parent.parent.name == 'Mozilla' else 'chromium'
            owned = json.loads(data) == host_manifest(root / 'soft-tracking-host', engine)
        return identity if owned and snapshot else owned
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException, ExpatError):
        return False


def remove_registrations(root):
    root = owned_root(root)
    home, uid = current_user()
    launch_agent = home / 'Library/LaunchAgents' / (LABEL + '.plist')
    for path in [launch_agent, *registration_paths()]:
        expected = owned_registration(path, root, snapshot=True)
        if not expected:
            continue
        if path == launch_agent:
            # Already-unloaded jobs are normal. Never target system/ or another UID.
            subprocess.run(['/bin/launchctl', 'bootout', 'gui/' + str(uid), str(path)],
                           capture_output=True, timeout=5, check=False)
        if owned_registration(path, root, snapshot=True) == expected:
            _remove_tree(path, expected=expected)


def bootloader_ancestors(uid):
    """Identify the onefile parent, not an arbitrary same-executable ancestor.

    Pinned PyInstaller 6.22.2 encodes the parent PID in _MEI%08xXXXXXX.
    Both that PID and the actual ancestry/executable/arguments must agree.
    """
    import psutil
    if not getattr(sys, 'frozen', False):
        return set()
    extraction = Path(getattr(sys, '_MEIPASS', ''))
    match = re.fullmatch(r'_MEI([0-9a-f]{8})[A-Za-z0-9_]{6}', extraction.name)
    if not match:
        return set()
    try:
        info = extraction.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
            return set()
        parent_pid = int(match[1], 16)
        for parent in psutil.Process(os.getpid()).parents():
            if (parent.pid == parent_pid and Path(parent.exe()) == Path(sys.executable) and
                    parent.uids().real == uid and parent.uids().effective == uid and
                    parent.cmdline()[1:] == sys.argv[1:]):
                return {parent.pid}
    except (OSError, psutil.Error):
        pass
    return set()


def stop_native_hosts(root):
    """Stop owned native hosts and wait for all other installed processes to exit."""
    import psutil
    _, uid = current_user()
    exempt = {os.getpid()} | bootloader_ancestors(uid)
    processes = []
    for process in psutil.process_iter():
        try:
            if process.pid in exempt or process.uids().real != uid or process.uids().effective != uid:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        try:
            executable = Path(process.exe())
            relative = executable.relative_to(root)
            if (relative == Path('soft-tracking-host') or
                    (len(relative.parts) == 4 and relative.parts[0] == 'versions' and
                     relative.parts[2:] == ('app', 'soft-tracking-host-app'))):
                process.terminate()
            processes.append(process)
        except (psutil.NoSuchProcess, ValueError):
            continue
        except psutil.AccessDenied:
            raise RuntimeError('Cannot verify current-user process exit') from None
    _, alive = psutil.wait_procs(processes, timeout=5)
    if alive:
        raise RuntimeError('Installed process still running')


def prepare(root):
    """Validate everything before flags/modes are changed or shutdown is requested."""
    root = owned_root(root)
    validate_tree(root.parent)
    home, uid = current_user()
    # Marker and lock creation require writable installation directories.
    for path in (root.parent, root):
        with _directory(home, path, uid) as descriptor:
            _repair(descriptor, os.fstat(descriptor))


def cleanup(root):
    root = owned_root(root)
    validate_tree(root.parent)
    wrapper = owned_wrapper(root)
    if wrapper is not None:
        _remove_tree(wrapper)
    _remove_tree(root.parent)


def dialog(title, message, cancel=None, remove=None):
    # All user-visible strings are argv data, never interpolated AppleScript.
    script = '''on run argv
display dialog (item 2 of argv) with title (item 1 of argv) buttons {"OK"} default button 1 with icon caution
end run'''
    args = [title, message]
    if cancel is not None:
        script = '''on run argv
try
display dialog (item 2 of argv) with title (item 1 of argv) buttons {item 3 of argv, item 4 of argv} default button 1 cancel button 1 with icon caution
return "remove"
on error number -128
return "cancel"
end try
end run'''
        args.extend([cancel, remove])
    result = subprocess.run(['/usr/bin/osascript', '-e', script, '--', *args],
                            capture_output=True, text=True, timeout=300, check=True)
    return result.stdout.strip() == 'remove'
