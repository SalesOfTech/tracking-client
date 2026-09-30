"""One-click Legacy migration inside the full installer, scoped to this OS user."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from . import installer, legacy_discovery
from .core.client import Client, workspace
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .core.networking import HttpClient
from .integration import autostart, protect_workspace
from .migrate_legacy import MigrationError, accept_enrollment


JOURNAL = 'automatic-migration.json'


def credentials(root, metadata):
    client = Client(root)
    http = HttpClient(Client.BASE_URL)
    try:
        result = http.post_json('/client/v3/migration_auto', dict(metadata, **client.device))
        if (not isinstance(result, dict) or result.get('ok') is not True
                or result.get('device_id') != client.device['device_id']
                or type(result.get('expires_at')) is not int
                or not time.time() < result['expires_at'] <= time.time() + 3700
                or not re.fullmatch('[a-f0-9]{32}', str(result.get('company_code', '')))
                or result.get('company_id') != metadata['company_id']
                or 'employee_key' in result):
            raise MigrationError('Invalid automatic migration response')
        client._enrollment_identity(result, client.device)
        return result
    finally:
        http.session.close()
        client.close()
        client.http.session.close()


def pending(root):
    journal = read_json(Path(root) / JOURNAL, {})
    return bool(journal and journal.get('state') != 'complete')


def recover_switch(root, previous):
    """Compensate a crash during retirement before attempting discovery again."""
    if previous.get('state') != 'switching':
        return previous
    snapshot = previous.get('legacy')
    if not isinstance(snapshot, dict) or Path(snapshot.get('root', '')).resolve() != root:
        raise installer.InstallerError('setup_migration_failed')
    journal, install_root = root / JOURNAL, root / 'install'
    with SingleInstance(root / 'migration.lock'):
        if read_json(journal, {}) != previous:
            raise installer.InstallerError('setup_migration_failed')
        try:
            with installer.stopped_supervisor(install_root):
                autostart(installer.installed_launcher(install_root), False)
                legacy_discovery.restore(snapshot, install_root)
            previous = dict(previous, state='rolled_back')
            atomic_json(journal, previous)
        except Exception as error:
            raise installer.InstallerError('setup_migration_failed') from error
    return previous


def try_migrate(bundle, language='', company_code='', on_phase=None):
    """Return (launcher, company code), or None when ordinary setup should continue."""
    root = workspace().resolve()
    journal = root / JOURNAL
    previous = read_json(journal, {})
    install_root = root / 'install'
    if (install_root / 'current.json').is_file() and not pending(root):
        return None
    previous = recover_switch(root, previous)
    snapshot = legacy_discovery.discover()
    if snapshot is None:
        if pending(root):
            raise installer.InstallerError('setup_migration_failed')
        return None
    if Path(snapshot['root']).resolve() != root:
        raise installer.InstallerError('setup_migration_failed')
    metadata = snapshot['metadata']
    binding = {'schema': 1, 'owner': snapshot['owner'],
               'source': hashlib.sha256(json.dumps(metadata, sort_keys=True).encode('utf-8')).hexdigest()}
    if previous and (any(previous.get(key) != value for key, value in binding.items())
                     or previous.get('state') not in ('preparing', 'failed_before_switch', 'rolled_back')):
        raise installer.InstallerError('setup_migration_failed')
    # Never adopt a preexisting v3 identity just because a Legacy process is present.
    if not previous and ((root / 'state.sqlite3').exists() or (install_root / 'current.json').exists()):
        return None
    if company_code and installer.resolve_company(company_code) != metadata['company_id']:
        raise installer.InstallerError('setup_company_conflict')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect_workspace(root)
    phase, launcher = 'preparing', None

    def save(state):
        atomic_json(journal, dict(binding, state=state, legacy=snapshot))

    with SingleInstance(root / 'migration.lock'):
        if read_json(journal, {}) != previous:
            raise installer.InstallerError('setup_migration_failed')
        try:
            legacy_discovery.revalidate(snapshot)
            save(phase)
            if on_phase:
                on_phase('migrating')
            redeemed = credentials(root, metadata)
            if install_root.exists() and not (install_root / 'current.json').exists():
                if install_root.is_symlink() or not install_root.resolve().is_relative_to(root):
                    raise MigrationError('Unsafe partial installation')
                install_root.rename(root / ('incomplete-install-' + uuid.uuid4().hex))
            launcher = installer.install(bundle, install_root, redeemed['company_code'],
                                         integrate=False, language=language, retire_legacy=False)
            client = Client(root)
            try:
                accept_enrollment(client, redeemed)
            finally:
                client.close()
                client.http.session.close()
            version = read_json(install_root / 'current.json')['version']
            installer.installed_health(install_root, version)
            legacy_discovery.revalidate(snapshot)
            installer.register_host(install_root / installer.executable_name(True, True), root)
            installer.shortcuts(install_root)
            installer.register_uninstaller(install_root)
            autostart(launcher, True)
            phase = 'switching'
            save(phase)
            legacy_discovery.retire(snapshot, install_root)
            save('complete')
            subprocess.Popen([str(launcher)])
            return launcher, redeemed['company_code']
        except Exception as error:
            recovery_failed = False
            if launcher is not None:
                try:
                    autostart(launcher, False)
                except Exception:
                    recovery_failed = True
            if phase == 'switching':
                try:
                    legacy_discovery.restore(snapshot, install_root)
                except Exception:
                    recovery_failed = True
            save('recovery_required' if recovery_failed else 'rolled_back' if phase == 'switching' else 'failed_before_switch')
            raise installer.InstallerError('setup_migration_failed') from error
