"""Offline frozen packaging smoke. All OS integration/network boundaries are blocked."""
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

import requests

from . import migrate_legacy as migration
from .core.client import Client
from .core.files import read_json


def run(bundle):
    with tempfile.TemporaryDirectory(prefix='soft-migration-smoke-') as temporary:
        profile = Path(temporary)
        root = profile / 'v3'
        metadata = dict(company_id=1, username='fixture', machine='fixture', os='windows', install_id='fixture')

        def redeemed(*args):
            client = Client(root)
            try:
                return dict(ok=True, company_code='a' * 32, device_id=client.device['device_id'],
                            company_id=1, user_id=7, company_name='Fixture', user_name='Fixture',
                            expires_at=int(time.time()) + 300)
            finally:
                client.close()
                client.http.session.close()

        def configuration(client, active, path, payload):
            assert path == '/client/v3/config'
            return dict(ok=True, device_id=active['device']['device_id'],
                        identity=dict(device_id=active['device']['device_id'], company_id=1,
                                      user_id=7, company_name='Fixture', user_name='Fixture'),
                        config={'policy_expires_at': int(time.time()) + 300, 'tracking': False})

        def retire(install, **scope):
            assert scope['session_id'] == 1
            client = Client(root)
            try:
                assert client.state.get('identity')['user_id'] == 7
                assert (install / 'current.json').is_file()
            finally:
                client.close()
                client.http.session.close()
            return {'state': 'complete'}

        # Installer signature/hash/layout verification is real; no real user API is called.
        with patch.object(requests.Session, 'request', side_effect=AssertionError('Network prohibited in smoke')), \
                patch.object(migration, 'capability', return_value='c' * 64), \
                patch.object(migration, 'user_context', return_value=(root, profile, 'fixture', 1)), \
                patch.object(migration, 'snapshot_legacy', return_value={'startup': None, 'files': [], 'running': []}), \
                patch.object(migration, 'discover_legacy', return_value=metadata), \
                patch.object(migration, 'protect_workspace'), \
                patch.object(migration, 'approved_credentials', side_effect=redeemed), \
                patch.object(Client, '_post', configuration), \
                patch.object(migration.installer, 'installed_health'), \
                patch.object(migration.installer, 'register_host'), \
                patch.object(migration.installer, 'shortcuts'), \
                patch.object(migration.installer, 'register_uninstaller'), \
                patch.object(migration, 'autostart'), \
                patch.object(migration.legacy_migration, 'replace_current_user', side_effect=retire), \
                patch.object(migration.subprocess, 'Popen'):
            journal = migration.migrate(bundle)
            assert read_json(journal)['state'] == 'complete'
