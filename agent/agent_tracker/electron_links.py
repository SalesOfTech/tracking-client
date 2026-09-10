"""Restore only bundle-internal macOS aliases described by the signed payload."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import time


MAP_NAME = 'electron-links.json'
BUNDLE_NAME = 'SoftTrackingUI.app'
MAX_MAP_BYTES = 64 * 1024
MAX_LINKS = 128
MAX_PATH_BYTES = 1024


def _parts(value, target=False):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > MAX_PATH_BYTES
            or '\\' in value or ':' in value or any(ord(char) < 32 for char in value)):
        raise ValueError('Invalid Electron link path')
    parts = value.split('/')
    if len(parts) > 64 or any(not part or len(part.encode('utf-8')) > 255
                              or (part not in ('.', '..') and part.endswith(('.', ' ')))
                              or (not target and part in ('.', '..')) for part in parts):
        raise ValueError('Invalid Electron link path')
    if not target and (len(parts) < 3 or parts[:2] != [BUNDLE_NAME, 'Contents']):
        raise ValueError('Electron aliases must stay inside the application bundle')
    return tuple(parts)


def _root(folder):
    root = Path(folder)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError('Electron bundle root must be a real directory')
    return root.resolve(strict=True)


def _real_directory(root, parts):
    path = root
    for part in parts:
        path = path / part
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError('Electron link parent must be a real directory')
    return path


def _existing(root, parts, target):
    parent = _real_directory(root, parts[:-1])
    path = parent / parts[-1]
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISLNK(mode) or os.readlink(path) != target:
        raise ValueError('Refusing to replace an existing Electron entry')
    return True


def plan_links(folder, document):
    """Validate the complete virtual tree before creating any alias; also used by CI."""
    root = _root(folder)
    if (not isinstance(document, dict) or set(document) != {'version', 'links'}
            or type(document['version']) is not int or document['version'] != 1
            or not isinstance(document['links'], list) or len(document['links']) > MAX_LINKS):
        raise ValueError('Invalid Electron link map')
    aliases, seen = {}, set()
    for entry in document['links']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'target'}:
            raise ValueError('Invalid Electron link entry')
        parts = _parts(entry['path'])
        _parts(entry['target'], target=True)
        folded = entry['path'].casefold()
        if folded in seen:
            raise ValueError('Duplicate Electron link path')
        seen.add(folded)
        aliases[parts] = entry['target']
    for parts, target in aliases.items():
        if any('/'.join(parts[:index]).casefold() in seen for index in range(1, len(parts))):
            raise ValueError('Electron link parent cannot be an alias')
        _existing(root, parts, target)

    resolved, visiting, order = {}, set(), []

    def resolve(parts):
        if parts in resolved:
            return resolved[parts]
        if parts in visiting:
            raise ValueError('Cyclic Electron links')
        visiting.add(parts)
        current = ()
        for part in parts[:-1] + _parts(aliases[parts], target=True):
            if not stat.S_ISDIR(root.joinpath(*current).lstat().st_mode):
                raise ValueError('Electron target traverses a regular file')
            if part in ('.', '..'):
                if part == '..':
                    if len(current) <= 1:
                        raise ValueError('Electron link escapes the application bundle')
                    current = current[:-1]
                continue
            candidate = current + (part,)
            if candidate[0] != BUNDLE_NAME:
                raise ValueError('Electron link escapes the application bundle')
            if candidate in aliases:
                candidate = resolve(candidate)
            path = root.joinpath(*candidate)
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError('Electron target contains an unlisted link or special file')
            current = candidate
        target = root.joinpath(*current)
        if target.resolve(strict=True) != target:
            raise ValueError('Electron link target is not canonical')
        resolved[parts] = current
        visiting.remove(parts)
        order.append(parts)
        return current

    for parts in aliases:
        resolve(parts)

    # Directory aliases can form traversal cycles without a direct symlink loop.
    checked, walking = set(), set()

    def check_directory_cycles(parts):
        if parts in walking:
            raise ValueError('Cyclic Electron directory links')
        if parts in checked:
            return
        walking.add(parts)
        target = resolved[parts]
        if root.joinpath(*target).is_dir():
            for child in aliases:
                if child[:len(target)] == target:
                    check_directory_cycles(child)
        walking.remove(parts)
        checked.add(parts)

    for parts in aliases:
        check_directory_cycles(parts)
    return [(parts, aliases[parts], resolved[parts]) for parts in order]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate Electron link map key')
        result[key] = value
    return result


@contextmanager
def _locked_map(root):
    path = root / MAP_NAME
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_MAP_BYTES:
        raise ValueError('Invalid Electron link map file')
    descriptor = os.open(path, os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0))
    with os.fdopen(descriptor, 'r+b') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('Electron link map changed while opening')
        def lock(unlock=False):
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)
        for attempt in range(101):
            try:
                lock()
                break
            except OSError:
                if attempt == 100:
                    raise RuntimeError('Electron bundle restoration is busy')
                time.sleep(0.05)
        try:
            stream.seek(0)
            raw = stream.read(MAX_MAP_BYTES + 1)
            if len(raw) > MAX_MAP_BYTES:
                raise ValueError('Electron link map is too large')
            yield json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        finally:
            lock(unlock=True)


def _create_link(root, parts, target, directory):
    _real_directory(root, parts[:-1])
    if os.symlink in os.supports_dir_fd:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(root, flags)
        try:
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            os.symlink(target, parts[-1], dir_fd=descriptor, target_is_directory=directory)
        finally:
            os.close(descriptor)
    else:
        os.symlink(target, root.joinpath(*parts), target_is_directory=directory)


def restore_links(folder):
    """Caller supplies its fixed packaged Electron root, never a renderer path."""
    root = _root(folder)
    if not os.path.lexists(root / MAP_NAME):
        return
    with _locked_map(root) as document:
        plan = plan_links(root, document)
        for parts, target, canonical in plan:
            path = root.joinpath(*canonical)
            if path.resolve(strict=True) != path:
                raise ValueError('Electron link target changed')
            if not _existing(root, parts, target):
                _create_link(root, parts, target, path.is_dir())
