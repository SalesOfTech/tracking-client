"""Local Legacy adapter; discovery is not enrollment or migration authorization.

discover() returns None or a JSON-serializable snapshot. root is the v3 workspace
(not the Legacy directory); install_root must be root/install. Windows owner and
session are the token account and RDP session. macOS uses the UID and gui/<uid>.
revalidate() raises on any observed change. retire() compensates partial failures;
restore() also supports rollback after a successful retirement. Keep the snapshot
until v3 activation is committed. Do not log snapshots or Legacy configuration.

Layout evidence: core/runtime_config.py, core/storage.py, main._base_envelope(),
and platform/macos.py. platform/linux.py was introduced with v3, not a shipped
Legacy layout. Linux discovery only rejects identified Legacy processes; it does
not guess directories, scan homes, or remove v3 desktop entries.
"""
from __future__ import annotations

from functools import wraps
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import stat
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import psutil

from . import legacy_migration, macos_legacy, migrate_legacy
from .core.files import atomic_json

MAX_METADATA = 65536
MAC_EXECUTABLE = 'SOFT Agent Tracking'
JOURNAL = 'legacy-discovery-retirement.json'


class LegacyDiscoveryError(migrate_legacy.MigrationError):
    """Static, non-secret failure safe to show to the installing user."""


def _safe_errors(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LegacyDiscoveryError:
            raise
        except Exception:
            raise LegacyDiscoveryError('legacy_discovery_failed') from None
    return call


def _system():
    return ('macos' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32'
            else 'linux' if sys.platform.startswith('linux') else 'unsupported')


def _exists(path):
    try:
        Path(path).lstat()
        return True
    except FileNotFoundError:
        return False


def _stamp(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_size, info.st_mtime_ns]


def _inspect(path, *, content=False):
    """Bound reads and reject special files and a file changed during the read."""
    path = Path(path)
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or getattr(before, 'st_file_attributes', 0) & 0x400):
        raise LegacyDiscoveryError('legacy_unsafe_file')
    evidence = {'stat': _stamp(before)}
    data = None
    if content:
        if before.st_size > MAX_METADATA:
            raise LegacyDiscoveryError('legacy_metadata_too_large')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            if _stamp(os.fstat(stream.fileno())) != evidence['stat']:
                raise LegacyDiscoveryError('legacy_changed')
            data = stream.read(MAX_METADATA + 1)
            if len(data) > MAX_METADATA or _stamp(os.fstat(stream.fileno())) != evidence['stat']:
                raise LegacyDiscoveryError('legacy_changed')
        evidence['sha256'] = hashlib.sha256(data).hexdigest()
    if _stamp(path.lstat()) != evidence['stat']:
        raise LegacyDiscoveryError('legacy_changed')
    return evidence, data


def _config(data):
    config = json.loads(data.decode('utf-8'))
    if not isinstance(config, dict):
        raise LegacyDiscoveryError('legacy_invalid_configuration')
    company = config.get('company_id')
    if isinstance(company, bool) or not re.fullmatch('[1-9][0-9]{0,9}', str(company)):
        raise LegacyDiscoveryError('legacy_invalid_company')
    endpoint = urlparse(str(config.get('base_url', '')))
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password:
        raise LegacyDiscoveryError('legacy_invalid_endpoint')
    return int(company)


def _windows_snapshot_context():
    # Absence must be checked before user_context rejects elevation/overrides.
    import win32api
    import win32con
    import win32profile
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        profile = Path(win32profile.GetUserProfileDirectory(token)).resolve()
        session = win32security.GetTokenInformation(token, win32security.TokenSessionId)
    finally:
        token.Close()
    return profile, psutil.Process().username().casefold(), session


def _windows_evidence(profile, legacy, root):
    configs = {Path(value).parent / 'agent_config.json' for value in legacy['files']}
    if len(configs) != 1:
        raise LegacyDiscoveryError('legacy_ambiguous_installation')
    config = configs.pop()
    install_id = root.parent / 'Agent' / 'install_id.txt'
    migrate_legacy.readable_legacy_path(config, profile)
    migrate_legacy.safe_path(install_id, profile)
    evidence = {}
    for value in legacy['files']:
        migrate_legacy.readable_legacy_path(value, profile)
        evidence[value] = _inspect(value)[0]
    evidence[str(config)], data = _inspect(config, content=True)
    _config(data)
    evidence[str(install_id)], data = _inspect(install_id, content=True)
    uuid.UUID(data.decode('utf-8').strip())
    return evidence


def _discover_windows():
    profile, owner, session = _windows_snapshot_context()
    legacy = migrate_legacy.snapshot_legacy(profile, owner, session)
    if not legacy['files'] and not legacy['startup'] and not legacy['running']:
        return None
    root, strict_profile, strict_owner, strict_session = migrate_legacy.user_context()
    if (profile, owner, session) != (strict_profile, strict_owner, strict_session):
        raise LegacyDiscoveryError('legacy_context_changed')
    evidence = _windows_evidence(profile, legacy, root)
    metadata = migrate_legacy.discover_legacy(profile, legacy, root)
    if _windows_evidence(profile, legacy, root) != evidence:
        raise LegacyDiscoveryError('legacy_changed')
    return dict(legacy, schema=1, platform='windows', root=str(root), profile=str(profile),
                owner=owner, session=session, metadata=metadata, evidence=evidence)


def _mac_account():
    import pwd
    uid = os.getuid()
    account = pwd.getpwuid(uid)
    return Path(account.pw_dir).absolute(), uid, account.pw_name


def _owned_path(path, home, uid):
    path, home = Path(path).absolute(), Path(home).absolute()
    if not path.is_relative_to(home):
        raise LegacyDiscoveryError('legacy_outside_current_home')
    for part in (path, *path.parents):
        if _exists(part):
            info = part.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                    or info.st_uid != uid):
                raise LegacyDiscoveryError('legacy_ownership_or_link')
        if part == home:
            break
    if not path.resolve().is_relative_to(home.resolve()):
        raise LegacyDiscoveryError('legacy_outside_current_home')


def _mac_context(home, uid, username):
    if (uid == 0 or os.geteuid() != uid or Path.home().resolve() != home.resolve()
            or os.environ.get('SOFT_TRACKING_INSTALL') or getpass.getuser() != username):
        raise LegacyDiscoveryError('legacy_conflicting_user_context')
    root = home / 'Library/Application Support/SOFT/TrackingV3'
    _owned_path(root, home, uid)
    return root


def _bundle_for(executable):
    executable = Path(executable)
    if (not executable.is_absolute() or executable.name != MAC_EXECUTABLE
            or executable.parent.name != 'MacOS' or executable.parent.parent.name != 'Contents'
            or executable.parent.parent.parent.name != macos_legacy.APP):
        raise LegacyDiscoveryError('legacy_unrecognized_executable')
    return executable.parent.parent.parent


def _mac_processes(uid):
    processes = []
    for process in psutil.process_iter():
        try:
            name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name != MAC_EXECUTABLE:
            continue
        try:
            owners = process.uids()
            if owners.real != uid:
                continue
            if owners.effective != uid:
                raise LegacyDiscoveryError('legacy_conflicting_process_owner')
            executable = process.exe()
            _bundle_for(executable)
            if process.cmdline() != [executable]:
                raise LegacyDiscoveryError('legacy_custom_launch_arguments')
            processes.append({'pid': process.pid, 'created': process.create_time(), 'exe': executable})
        except psutil.NoSuchProcess:
            continue
    return sorted(processes, key=lambda value: value['pid'])


def _launchctl(*args):
    # Never collect launchd's environment/configuration output in logs or snapshots.
    return subprocess.run(['/bin/launchctl', *args], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=15, check=False).returncode


def _job_loaded(session):
    status = _launchctl('print', session + '/' + macos_legacy.LABEL)
    if status not in (0, 113):
        raise LegacyDiscoveryError('legacy_launchd_inspection_failed')
    return status == 0


def _discover_macos():
    home, uid, username = _mac_account()
    startup = home / 'Library/LaunchAgents' / (macos_legacy.LABEL + '.plist')
    candidates = {path for path in (home / 'Applications' / macos_legacy.APP,
                                    home / 'Downloads' / macos_legacy.APP) if _exists(path)}
    shared = Path('/Applications') / macos_legacy.APP
    if _exists(shared):
        raise LegacyDiscoveryError('legacy_shared_install_requires_review')
    evidence, startup_record = {}, None
    if _exists(startup):
        _owned_path(startup, home, uid)
        evidence[str(startup)], data = _inspect(startup, content=True)
        info = plistlib.loads(data)
        if (not isinstance(info, dict) or info.get('Label') != macos_legacy.LABEL
                or set(info) - {'Label', 'ProgramArguments', 'RunAtLoad', 'KeepAlive'}
                or not isinstance(info.get('ProgramArguments'), list)
                or len(info['ProgramArguments']) != 1
                or not isinstance(info['ProgramArguments'][0], str)
                or any(type(info[key]) is not bool for key in ('RunAtLoad', 'KeepAlive') if key in info)):
            raise LegacyDiscoveryError('legacy_unrecognized_startup')
        candidates.add(_bundle_for(info['ProgramArguments'][0]))
        startup_record = {'path': str(startup)}
    processes = _mac_processes(uid)
    candidates.update(_bundle_for(process['exe']) for process in processes)
    if not candidates:
        return None
    if len(candidates) != 1:
        raise LegacyDiscoveryError('legacy_ambiguous_installation')
    root = _mac_context(home, uid, username)
    bundle = candidates.pop()
    executable = bundle / 'Contents/MacOS' / MAC_EXECUTABLE
    config = executable.with_name('agent_config.json')
    install_id = home / 'Library/Application Support/SOFT/Agent/install_id.txt'
    info_path = bundle / 'Contents/Info.plist'
    for path in (bundle, executable, config, install_id, info_path):
        _owned_path(path, home, uid)
    if not bundle.is_dir():
        raise LegacyDiscoveryError('legacy_unrecognized_bundle')
    evidence[str(executable)], _ = _inspect(executable)
    evidence[str(info_path)], data = _inspect(info_path, content=True)
    info = plistlib.loads(data)
    if not isinstance(info, dict) or info.get('CFBundleExecutable') != MAC_EXECUTABLE:
        raise LegacyDiscoveryError('legacy_unrecognized_bundle')
    evidence[str(config)], data = _inspect(config, content=True)
    company_id = _config(data)
    evidence[str(install_id)], data = _inspect(install_id, content=True)
    identifier = str(uuid.UUID(data.decode('utf-8').strip()))
    machine = platform.node()
    if any(not isinstance(value, str) or not value or len(value) > 255
           or any(ord(character) < 32 for character in value) for value in (username, machine)):
        raise LegacyDiscoveryError('legacy_invalid_account_identity')
    session = 'gui/' + str(uid)
    loaded = _job_loaded(session)
    if loaded and startup_record is None:
        raise LegacyDiscoveryError('legacy_launchd_without_startup')
    return dict(schema=1, platform='macos', root=str(root), profile=str(home), owner=uid,
                session=session, bundle=str(bundle), files=[str(executable)], startup=startup_record,
                loaded=loaded, running=sorted({process['exe'] for process in processes}),
                processes=processes, evidence=evidence,
                metadata=dict(company_id=company_id, install_id=identifier, username=username,
                              machine=machine, os='macos'))


def _discover_linux():
    # These are established Legacy names, not an invented Linux installation layout.
    for process in psutil.process_iter():
        try:
            name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name not in (MAC_EXECUTABLE, 'soft_agent') and not legacy_migration.LEGACY_NAME.fullmatch(name):
            continue
        try:
            if process.uids().real == os.getuid():
                raise LegacyDiscoveryError('legacy_linux_install_unsupported')
        except psutil.NoSuchProcess:
            continue
    return None


@_safe_errors
def discover():
    """Read only. Return None for absence; raise for unsupported/unsafe evidence."""
    system = _system()
    if system == 'windows':
        return _discover_windows()
    if system == 'macos':
        return _discover_macos()
    if system == 'linux':
        return _discover_linux()
    raise LegacyDiscoveryError('legacy_platform_unsupported')


@_safe_errors
def revalidate(snapshot):
    """Return the unchanged snapshot, or raise (including absent -> present)."""
    if discover() != snapshot:
        raise LegacyDiscoveryError('legacy_changed')
    return snapshot


def _scope(snapshot, install_root):
    if snapshot.get('schema') != 1 or snapshot['platform'] != _system():
        raise LegacyDiscoveryError('legacy_context_changed')
    root, profile = Path(snapshot['root']), Path(snapshot['profile'])
    install_root = Path(install_root).absolute()
    if install_root != root / 'install':
        raise LegacyDiscoveryError('legacy_wrong_install_root')
    if snapshot['platform'] == 'windows':
        if migrate_legacy.user_context() != (root, profile, snapshot['owner'], snapshot['session']):
            raise LegacyDiscoveryError('legacy_context_changed')
        migrate_legacy.safe_path(install_root, profile)
    elif snapshot['platform'] == 'macos':
        home, uid, username = _mac_account()
        if (profile != home or snapshot['owner'] != uid or snapshot['session'] != 'gui/' + str(uid)
                or _mac_context(home, uid, username) != root or snapshot['metadata']['username'] != username):
            raise LegacyDiscoveryError('legacy_context_changed')
        _owned_path(install_root, home, uid)
        if install_root.is_relative_to(Path(snapshot['bundle'])):
            raise LegacyDiscoveryError('legacy_wrong_install_root')
    else:
        raise LegacyDiscoveryError('legacy_platform_unsupported')
    if not install_root.is_dir():
        raise LegacyDiscoveryError('legacy_install_root_missing')
    return install_root


def _check_evidence(snapshot, moves=()):
    for value, expected in snapshot['evidence'].items():
        path = Path(value)
        for source, backup in moves:
            if path == source or path.is_relative_to(source):
                path = backup / path.relative_to(source) if path != source else backup
                break
        if snapshot['platform'] == 'macos':
            _owned_path(path, Path(snapshot['profile']), snapshot['owner'])
        else:
            migrate_legacy.readable_legacy_path(path, Path(snapshot['profile']))
        if _inspect(path, content='sha256' in expected)[0] != expected:
            raise LegacyDiscoveryError('legacy_changed')


def _binding(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode('utf-8')).hexdigest()


def _journal_path(snapshot, install_root):
    path = install_root / JOURNAL
    _owned_path(path, Path(snapshot['profile']), snapshot['owner'])
    return path


def _read_report(snapshot, install_root, *, allow_rolled_back=False):
    path = _journal_path(snapshot, install_root)
    if not _exists(path):
        return None
    _, data = _inspect(path, content=True)
    report = json.loads(data.decode('utf-8'))
    if (not isinstance(report, dict)
            or not re.fullmatch('[a-f0-9]{64}', str(report.get('binding', '')))
            or not re.fullmatch('legacy-retirement-[a-f0-9]{32}', str(report.get('backup', '')))
            or report.get('state') not in ('prepared', 'complete', 'rolled_back', 'recovery_required')):
        raise LegacyDiscoveryError('legacy_invalid_rollback_journal')
    if report['binding'] != _binding(snapshot) and not (allow_rolled_back and report['state'] == 'rolled_back'):
        raise LegacyDiscoveryError('legacy_invalid_rollback_journal')
    return report


def _moves(snapshot, install_root, report):
    backup = install_root / report['backup']
    _owned_path(backup, Path(snapshot['profile']), snapshot['owner'])
    moves = [(Path(snapshot['bundle']), backup / macos_legacy.APP)]
    if snapshot['startup']:
        moves.append((Path(snapshot['startup']['path']), backup / (macos_legacy.LABEL + '.plist')))
    return moves


def _retire_macos(snapshot, install_root):
    previous = _read_report(snapshot, install_root, allow_rolled_back=True)
    if previous and previous['state'] != 'rolled_back':
        raise LegacyDiscoveryError('legacy_existing_retirement_requires_review')
    report = dict(binding=_binding(snapshot), backup='legacy-retirement-' + uuid.uuid4().hex,
                  state='prepared')
    journal = _journal_path(snapshot, install_root)
    backup = install_root / report['backup']
    _owned_path(backup, Path(snapshot['profile']), snapshot['owner'])
    backup.mkdir(mode=0o700)
    atomic_json(journal, report)
    try:
        if snapshot['loaded']:
            if _launchctl('bootout', snapshot['session'] + '/' + macos_legacy.LABEL) != 0:
                raise LegacyDiscoveryError('legacy_launchd_retirement_failed')
            if _job_loaded(snapshot['session']):
                raise LegacyDiscoveryError('legacy_launchd_still_running')
        for record in snapshot['processes']:
            try:
                process = psutil.Process(record['pid'])
                if (process.create_time() != record['created'] or process.uids().real != snapshot['owner']
                        or process.uids().effective != snapshot['owner'] or process.exe() != record['exe']
                        or process.cmdline() != [record['exe']]):
                    raise LegacyDiscoveryError('legacy_process_changed')
                process.kill()
                process.wait(timeout=10)
            except psutil.NoSuchProcess:
                continue
        if _mac_processes(snapshot['owner']):
            raise LegacyDiscoveryError('legacy_process_still_running')
        _check_evidence(snapshot)
        # Preserve original files, including permissions; no recursive copies/deletes.
        for source, destination in reversed(_moves(snapshot, install_root, report)):
            _owned_path(destination, Path(snapshot['profile']), snapshot['owner'])
            if _exists(destination):
                raise LegacyDiscoveryError('legacy_rollback_collision')
            source.rename(destination)
        report['state'] = 'complete'
        atomic_json(journal, report)
        return report
    except Exception:
        try:
            _restore_macos(snapshot, install_root)
        except Exception:
            report['state'] = 'recovery_required'
            atomic_json(journal, report)
            raise LegacyDiscoveryError('legacy_recovery_required') from None
        raise LegacyDiscoveryError('legacy_retirement_failed_rolled_back') from None


def _restore_macos(snapshot, install_root):
    report = _read_report(snapshot, install_root)
    if report is None:
        return {'state': 'not_retired'}
    moves = _moves(snapshot, install_root, report)
    displaced = []
    for source, backup in moves:
        for path in (source, backup):
            _owned_path(path, Path(snapshot['profile']), snapshot['owner'])
        if _exists(source) == _exists(backup):
            raise LegacyDiscoveryError('legacy_rollback_collision')
        if _exists(backup):
            displaced.append((source, backup))
    _check_evidence(snapshot, displaced)
    active = _mac_processes(snapshot['owner'])
    if any(process['exe'] not in snapshot['files'] for process in active):
        raise LegacyDiscoveryError('legacy_process_changed')
    # Check every destination first, then restore the executable before its startup.
    for source, backup in displaced:
        backup.rename(source)
    if snapshot['loaded']:
        if not _job_loaded(snapshot['session']):
            if _launchctl('bootstrap', snapshot['session'], snapshot['startup']['path']) != 0:
                raise LegacyDiscoveryError('legacy_launchd_restore_failed')
            if not _job_loaded(snapshot['session']):
                raise LegacyDiscoveryError('legacy_launchd_restore_failed')
    elif snapshot['running'] and not active:
        executable = snapshot['files'][0]
        subprocess.Popen([executable], cwd=str(Path(executable).parent))
    report['state'] = 'rolled_back'
    atomic_json(_journal_path(snapshot, install_root), report)
    return report


@_safe_errors
def retire(snapshot, install_root):
    """Call only after authenticated v3 enrollment and health checks succeed."""
    revalidate(snapshot)
    if snapshot is None:
        return {'state': 'not_found'}
    install_root = _scope(snapshot, install_root)
    if snapshot['platform'] == 'macos':
        return _retire_macos(snapshot, install_root)
    try:
        return legacy_migration.replace_current_user(
            install_root, session_id=snapshot['session'], expected_files=snapshot['files'])
    except Exception:
        try:
            restore(snapshot, install_root)
        except Exception:
            raise LegacyDiscoveryError('legacy_recovery_required') from None
        raise LegacyDiscoveryError('legacy_retirement_failed_rolled_back') from None


@_safe_errors
def restore(snapshot, install_root):
    """Restore only the captured user/session; never overwrite changed paths."""
    if snapshot is None:
        return {'state': 'not_found'}
    install_root = _scope(snapshot, install_root)
    if snapshot['platform'] == 'macos':
        return _restore_macos(snapshot, install_root)
    _check_evidence(snapshot)
    metadata = migrate_legacy.discover_legacy(Path(snapshot['profile']), snapshot, Path(snapshot['root']))
    if metadata != snapshot['metadata']:
        raise LegacyDiscoveryError('legacy_changed')
    migrate_legacy.restore_legacy(snapshot, session_id=snapshot['session'])
    return {'state': 'rolled_back'}
