"""Personalized migration. Legacy metadata is never an authentication credential."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import psutil

from . import installer, legacy_migration
from .core.client import Client
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .core.networking import HttpClient
from .integration import autostart, protect_workspace


class MigrationError(ValueError):
    """A deliberately non-secret operator-facing failure."""


def user_context():
    """Use token/profile APIs, not inherited USERNAME/LOCALAPPDATA from elevation."""
    if os.name != 'nt':
        raise MigrationError('Windows interactive user session required')
    import win32api
    import win32con
    import win32profile
    import win32security
    import win32ts
    from win32com.shell import shell, shellcon

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        if win32security.GetTokenInformation(token, win32security.TokenElevation):
            raise MigrationError('Run without elevation in the employee Windows session')
        session = win32security.GetTokenInformation(token, win32security.TokenSessionId)
        if not session or session != win32ts.ProcessIdToSessionId(os.getpid()):
            raise MigrationError('Interactive employee session required')
        profile = Path(win32profile.GetUserProfileDirectory(token)).resolve()
        local = Path(shell.SHGetFolderPath(0, shellcon.CSIDL_LOCAL_APPDATA, token, 0))
    finally:
        token.Close()
    root = local / 'SOFT' / 'TrackingV3'
    safe_path(root, profile)
    if Path.home().resolve() != profile or os.environ.get('SOFT_TRACKING_INSTALL'):
        raise MigrationError('Conflicting user profile or installation override')
    if Path(os.environ.get('APPDATA', '')).resolve() != Path(
            shell.SHGetFolderPath(0, shellcon.CSIDL_APPDATA, 0, 0)).resolve():
        raise MigrationError('Conflicting roaming profile')
    return root, profile, psutil.Process().username().casefold(), session


def safe_path(path, profile):
    path = Path(path).absolute()
    if not path.resolve().is_relative_to(profile.resolve()):
        raise ValueError('Path outside current Windows profile')
    for part in (path, *path.parents):
        if part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse points are not supported for migration')
        if part == profile:
            break


def capability(executable, bundle):
    match = re.fullmatch(r'SOFT-Tracking-Migrate-(windows-(?:x64|x86|arm64)-(?:modern|legacy))_([a-f0-9]{64})(?: \(\d+\))?\.exe',
                         Path(executable).name)
    if not match or read_json(Path(bundle) / 'setup-build.json', {}).get('target') != match[1]:
        raise MigrationError('Download the personalized migration tool from CRM; keep its original filename')
    return match[2]


def discover_legacy(profile, snapshot, root):
    import win32api
    import win32con
    import win32security
    paths = {Path(value).parent / 'agent_config.json' for value in snapshot['files']}
    if len(paths) != 1:
        raise MigrationError('Missing or ambiguous Legacy installation')
    config_path = paths.pop()
    install_path = root.parent / 'Agent' / 'install_id.txt'
    for path in (config_path, install_path):
        safe_path(path, profile)
        if not path.is_file() or path.stat().st_size > 65536:
            raise MigrationError('Missing or invalid Legacy configuration')
    config = read_json(config_path, {})
    company = config.get('company_id')
    if isinstance(company, bool) or not re.fullmatch('[1-9][0-9]{0,9}', str(company)):
        raise MigrationError('Invalid Legacy company identifier')
    # A locally modified Legacy endpoint must never receive v3 credentials.
    endpoint = urlparse(str(config.get('base_url', '')))
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password:
        raise MigrationError('Invalid Legacy server configuration')
    try:
        install_id = str(uuid.UUID(install_path.read_text(encoding='utf-8').strip()))
    except (ValueError, UnicodeError):
        raise MigrationError('Invalid Legacy install ID') from None
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        username, _domain, _kind = win32security.LookupAccountSid(None, sid)
    finally:
        token.Close()
    machine = win32api.GetComputerName()
    if not username or not machine:
        raise MigrationError('Cannot identify the current Windows account')
    return {'company_id': int(company), 'install_id': install_id,
            'username': username, 'machine': machine, 'os': 'windows'}


def snapshot_legacy(profile, owner, session):
    import winreg
    import win32ts
    startup = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, legacy_migration.RUN_KEY) as key:
            startup = list(winreg.QueryValueEx(key, 'SOFTAgent'))
    except FileNotFoundError:
        pass
    files, running = set(), set()
    if startup:
        path = legacy_migration.startup_executable(startup[0])
        if path is None or startup[1] != winreg.REG_SZ:
            raise ValueError('Unrecognized Legacy startup; manual review required')
        files.add(path)
    for process in psutil.process_iter():
        try:
            name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not legacy_migration.LEGACY_NAME.fullmatch(name):
            continue
        try:
            if process.username().casefold() != owner:
                continue
            if win32ts.ProcessIdToSessionId(process.pid) != session:
                raise ValueError('Legacy is running in another session of this account')
            if len(process.cmdline()) != 1:
                raise MigrationError('Custom Legacy launch arguments require administrator review')
            path = Path(process.exe())
            files.add(path)
            running.add(path)
        except psutil.NoSuchProcess:
            continue
    for path in files:
        safe_path(path, profile)
        if not legacy_migration.removable(path, profile) or path.with_name(path.name + '.legacy-disabled').exists():
            raise ValueError('Legacy file is shared, missing or already disabled; manual review required')
    return {'startup': startup, 'files': sorted(map(str, files)), 'running': sorted(map(str, running))}


def restore_legacy(snapshot):
    import winreg
    for value in snapshot['files']:
        path = Path(value)
        disabled = path.with_name(path.name + '.legacy-disabled')
        if disabled.exists():
            if path.exists():
                raise ValueError('Rollback destination changed; manual recovery required')
            disabled.rename(path)
    if snapshot['startup']:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, legacy_migration.RUN_KEY) as key:
            try:
                current = winreg.QueryValueEx(key, 'SOFTAgent')
            except FileNotFoundError:
                current = None
            if current and list(current) != list(snapshot['startup']):
                raise ValueError('Legacy startup changed; manual recovery required')
            command, kind = snapshot['startup']
            winreg.SetValueEx(key, 'SOFTAgent', 0, kind, command)
    active = set()
    owner = psutil.Process().username().casefold()
    for process in psutil.process_iter():
        try:
            if process.username().casefold() == owner:
                active.add(process.exe())
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            continue
    for value in snapshot['running']:
        if value not in active:
            subprocess.Popen([value], cwd=str(Path(value).parent))


def approved_credentials(root, metadata, grant):
    client = Client(root)
    http = HttpClient(Client.BASE_URL)
    try:
        result = http.post_json('/client/v3/migration_redeem',
                                dict(metadata, **client.device, migration_token=grant))
        if (not isinstance(result, dict) or result.get('ok') is not True
                or result.get('device_id') != client.device['device_id']
                or type(result.get('expires_at')) is not int or result['expires_at'] <= time.time()
                or not re.fullmatch('[a-f0-9]{32}', str(result.get('company_code', '')))
                or result.get('company_id') != metadata['company_id']):
            raise MigrationError('Invalid or expired migration approval response')
        client._enrollment_identity(result, client.device)
        return result
    finally:
        http.session.close()
        client.close()
        client.http.session.close()


def accept_enrollment(client, redeemed):
    """Commit identity only after device-authenticated policy confirms the grant."""
    profile = client.profiles.active()
    identity = client._enrollment_identity(redeemed, profile['device'])
    company_hash = hashlib.sha256(redeemed['company_code'].encode('ascii')).hexdigest()
    if profile['company_hash'] and profile['company_hash'] != company_hash:
        raise MigrationError('Migration cannot change the installed company')
    if profile['identity'] and any(profile['identity'].get(k) != identity[k]
                                   for k in ('device_id', 'company_id', 'user_id')):
        raise MigrationError('Migration cannot switch employees')
    result = client._post(profile, '/client/v3/config', {'version': read_json(
        client.state.root / 'install' / 'current.json', {}).get('version', '')})
    policy = result.get('config') if isinstance(result, dict) else None
    confirmed = result.get('identity') if isinstance(result, dict) else None
    if (not isinstance(result, dict) or result.get('ok') is not True
            or result.get('device_id') != identity['device_id'] or not isinstance(policy, dict)
            or type(policy.get('policy_expires_at')) is not int
            or not time.time() < policy['policy_expires_at'] <= time.time() + 3700
            or not isinstance(confirmed, dict)
            or any(confirmed.get(k) != identity[k] for k in ('device_id', 'company_id', 'user_id'))
            or any(not isinstance(confirmed.get(k), str) for k in ('company_name', 'user_name'))):
        raise MigrationError('Fresh device configuration did not confirm migration identity')
    if not client.profiles.update_active(profile['epoch'], identity=identity,
                                         company_code_hash=company_hash, policy=policy):
        raise MigrationError('Employee changed while accepting migration')


def migrate(bundle):
    grant = capability(sys.executable, bundle)
    root, profile, owner, session = user_context()
    initial = snapshot_legacy(profile, owner, session)
    metadata = discover_legacy(profile, initial, root)
    install_root = root / 'install'
    journal = root / 'standalone-migration.json'
    binding = {'schema': 1, 'tool': 'standalone-legacy-migration', 'root': str(root),
               'owner': owner, 'session': session,
               'metadata_binding': hashlib.sha256(json.dumps(metadata, sort_keys=True).encode('utf-8')).hexdigest()}
    resume = root.exists()
    if resume:
        previous = read_json(journal, {})
        if (not isinstance(previous, dict) or any(previous.get(k) != v for k, v in binding.items())
                or previous.get('state') not in ('failed_before_switch', 'rolled_back', 'preparing')
                or previous.get('legacy') != initial):
            raise MigrationError('Existing v3 workspace is not a resumable migration; contact administrator')
        for path in root.rglob('*'):
            safe_path(path, profile)
    else:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        protect_workspace(root)
    phase = 'preparing'
    launcher = None

    def save(state):
        atomic_json(journal, dict(binding, state=state, legacy=initial))

    with SingleInstance(root / 'migration.lock'):
        if resume and read_json(journal, {}) != previous:
            raise MigrationError('Migration journal changed; another migration may be active')
        try:
            save(phase)
            redeemed = approved_credentials(root, metadata, grant)
            if resume and install_root.exists() and not (install_root / 'current.json').exists():
                # Keep partial files for diagnosis; device state lives outside install/.
                safe_path(install_root, profile)
                install_root.rename(root / ('incomplete-install-' + uuid.uuid4().hex))
            launcher = installer.install(bundle, install_root, redeemed['company_code'],
                                         integrate=False, retire_legacy=False)
            client = Client(root)
            try:
                accept_enrollment(client, redeemed)
            finally:
                client.close()
                client.http.session.close()
            version = read_json(install_root / 'current.json')['version']
            installer.installed_health(install_root, version)
            if (user_context() != (root, profile, owner, session)
                    or snapshot_legacy(profile, owner, session) != initial
                    or discover_legacy(profile, initial, root) != metadata):
                raise ValueError('Legacy changed during preparation; retry after review')
            installer.register_host(install_root / installer.executable_name(True, True), root)
            installer.shortcuts(install_root)
            installer.register_uninstaller(install_root)
            autostart(launcher, True)
            phase = 'switching'
            save(phase)
            legacy_migration.replace_current_user(install_root)
            subprocess.Popen([str(launcher)])
            phase = 'complete'
        except Exception:
            if launcher is not None:
                try:
                    autostart(launcher, False)
                except Exception:
                    phase = 'recovery_required'
            if phase in ('switching', 'recovery_required'):
                try:
                    restore_legacy(initial)
                    phase = 'rolled_back' if phase == 'switching' else phase
                except Exception:
                    phase = 'recovery_required'
            save(phase if phase != 'preparing' else 'failed_before_switch')
            raise
        save(phase)
    return journal


def main():
    from .migration_messages import MESSAGES
    from .i18n import detect_language
    parser = argparse.ArgumentParser(prog='SOFT-Tracking-Migrate', description='Migrate Legacy in the current employee Windows session. Do not elevate.')
    parser.add_argument('--bundle', type=Path, default=Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload')
    parser.add_argument('--language', choices=tuple(MESSAGES), default=detect_language())
    parser.add_argument('--self-test', action='store_true', help='Offline isolated packaging smoke; never migrate a real account')
    args = parser.parse_args()
    messages = MESSAGES.get(args.language, MESSAGES['en'])
    print(messages['intro'])
    result = 0
    try:
        # Frozen packages always use their embedded payload/keyring.
        bundle = Path(sys._MEIPASS) / 'setup-payload' if getattr(sys, 'frozen', False) else args.bundle
        if args.self_test:
            from .migration_smoke import run
            run(bundle)
            print('PASS: isolated frozen migration smoke')
            return 0
        migrate(bundle)
    except Exception:
        # Never echo exceptions, EXE path, request payload or the filename capability.
        print(messages['failed'])
        result = 1
    else:
        print(messages['complete'])
    if getattr(sys, 'frozen', False) and sys.stdin.isatty():
        input(messages['close'])
    return result


if __name__ == '__main__':
    raise SystemExit(main())
