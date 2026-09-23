import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import migrate_legacy as migration
from agent_tracker.core.files import read_json


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.root = self.profile / 'v3'
        self.events = []
        self.snapshot = {'startup': ['"fixture.exe"', 1], 'files': [], 'running': []}
        self.mocks = {}
        for name in ('user_context', 'snapshot_legacy', 'protect_workspace', 'Client',
                     'autostart', 'restore_legacy', 'capability', 'discover_legacy', 'approved_credentials', 'accept_enrollment'):
            patcher = patch.object(migration, name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks['user_context'].return_value = (self.root, self.profile, 'domain\\employee', 4)
        self.mocks['snapshot_legacy'].return_value = self.snapshot
        self.mocks['capability'].return_value = 'c' * 64
        self.mocks['discover_legacy'].return_value = {'company_id': 1, 'install_id': 'test', 'username': 'employee', 'machine': 'pc', 'os': 'windows'}
        self.redeemed = dict(company_code='a' * 32, device_id='d' * 32, company_id=1, user_id=7)
        self.mocks['approved_credentials'].return_value = self.redeemed
        self.client = self.mocks['Client'].return_value
        self.client.profiles.active.return_value = {'identity': None}
        for name in ('install', 'installed_health', 'register_host', 'shortcuts', 'register_uninstaller'):
            patcher = patch.object(migration.installer, name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.launcher = self.root / 'install' / 'launcher.exe'
        self.mocks['install'].return_value = self.launcher
        for target, name in ((migration.legacy_migration, 'replace_current_user'),
                             (migration.subprocess, 'Popen')):
            patcher = patch.object(target, name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        real_read = migration.read_json
        patcher = patch.object(migration, 'read_json', side_effect=lambda path, *args: (
            {'version': '3.2.0'} if Path(path).name == 'current.json' else real_read(path, *args)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_migration(self):
        return migration.migrate(self.profile / 'bundle')

    def journal(self):
        return read_json(self.root / 'standalone-migration.json')

    def test_success_order_and_explicit_suppression(self):
        for name, mock in [('redeem', self.mocks['approved_credentials']), ('install', self.mocks['install']),
                           ('config', self.mocks['accept_enrollment']), ('health', self.mocks['installed_health']),
                           ('retire', self.mocks['replace_current_user']), ('launch', self.mocks['Popen'])]:
            def record(*args, _name=name, **kwargs):
                self.events.append(_name)
                return self.launcher if _name == 'install' else self.redeemed if _name == 'redeem' else None
            mock.side_effect = record
        self.run_migration()
        self.assertEqual(self.events, ['redeem', 'install', 'config', 'health', 'retire', 'launch'])
        self.client.enroll.assert_not_called()
        self.mocks['install'].assert_called_once_with(self.profile / 'bundle', self.root / 'install',
                                                    'a' * 32, integrate=False, retire_legacy=False)
        self.assertEqual(self.journal()['state'], 'complete')
        self.mocks['register_uninstaller'].assert_called_once_with(self.root / 'install')
        self.assertNotIn('b' * 64, (self.root / 'standalone-migration.json').read_text())

    def test_invalid_capability_does_not_create_workspace(self):
        self.mocks['capability'].side_effect = migration.MigrationError('invalid download')
        with self.assertRaises(migration.MigrationError):
            self.run_migration()
        self.mocks['user_context'].assert_not_called()
        self.assertFalse(self.root.exists())

    def test_existing_workspace_is_never_reenrolled(self):
        self.root.mkdir()
        with self.assertRaisesRegex(migration.MigrationError, 'Existing v3'):
            self.run_migration()
        self.mocks['install'].assert_not_called()

    def test_verification_failure_keeps_legacy(self):
        self.mocks['install'].side_effect = ValueError('invalid signature')
        with self.assertRaises(ValueError):
            self.run_migration()
        self.client.enroll.assert_not_called()
        self.mocks['replace_current_user'].assert_not_called()
        self.mocks['Popen'].assert_not_called()

    def test_enrollment_failure_keeps_legacy(self):
        self.mocks['accept_enrollment'].side_effect = ValueError('rejected')
        with self.assertRaises(ValueError):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()
        self.assertEqual(self.journal()['state'], 'failed_before_switch')
        self.client.close.assert_called_once()

    def test_existing_identity_cannot_bypass_config_validation(self):
        self.client.profiles.active.return_value = {'identity': {'user_id': 1}}
        self.mocks['accept_enrollment'].side_effect = ValueError('fresh config rejected')
        with self.assertRaisesRegex(ValueError, 'fresh config rejected'):
            self.run_migration()
        self.client.enroll.assert_not_called()
        self.mocks['replace_current_user'].assert_not_called()

    def test_policy_failure_keeps_legacy(self):
        self.mocks['accept_enrollment'].side_effect = ValueError('revoked')
        with self.assertRaises(ValueError):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()

    def test_health_failure_keeps_legacy(self):
        self.mocks['installed_health'].side_effect = ValueError('health failed')
        with self.assertRaises(ValueError):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()

    def test_changed_legacy_blocks_switch(self):
        self.mocks['snapshot_legacy'].side_effect = [self.snapshot, dict(self.snapshot, files=['changed'])]
        with self.assertRaisesRegex(ValueError, 'changed during'):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()

    def test_integration_failure_keeps_legacy(self):
        self.mocks['register_host'].side_effect = OSError('registry')
        with self.assertRaises(OSError):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()

    def test_partial_retirement_rolls_back(self):
        self.mocks['replace_current_user'].side_effect = OSError('locked file')
        with self.assertRaises(OSError):
            self.run_migration()
        self.mocks['restore_legacy'].assert_called_once_with(self.snapshot)
        self.assertEqual(self.journal()['state'], 'rolled_back')
        self.mocks['Popen'].assert_not_called()

    def test_launcher_failure_rolls_back(self):
        self.mocks['Popen'].side_effect = OSError('launch failed')
        with self.assertRaises(OSError):
            self.run_migration()
        self.assertEqual(self.journal()['state'], 'rolled_back')
        self.mocks['autostart'].assert_called_with(self.launcher, False)

    def test_failed_rollback_requires_operator(self):
        self.mocks['replace_current_user'].side_effect = OSError('locked file')
        self.mocks['restore_legacy'].side_effect = OSError('locked rollback')
        with self.assertRaises(OSError):
            self.run_migration()
        self.assertEqual(self.journal()['state'], 'recovery_required')

    def test_transient_enrollment_failure_resumes_own_root(self):
        self.mocks['accept_enrollment'].side_effect = [OSError('offline'), None]
        with self.assertRaises(OSError):
            self.run_migration()
        self.run_migration()
        self.assertEqual(self.journal()['state'], 'complete')
        self.assertEqual(self.mocks['accept_enrollment'].call_count, 2)

    def test_resume_reauthenticates_saved_identity(self):
        self.mocks['installed_health'].side_effect = [OSError('health'), None]
        with self.assertRaises(OSError):
            self.run_migration()
        identity = {'device_id': 'd' * 32, 'company_id': 1, 'user_id': 7}
        self.client.profiles.active.return_value = {'identity': identity}
        self.run_migration()
        self.assertEqual(self.mocks['approved_credentials'].call_count, 2)
        self.assertEqual(self.mocks['accept_enrollment'].call_count, 2)
        self.assertEqual(self.journal()['state'], 'complete')

    def test_resume_rejects_changed_employee(self):
        self.mocks['installed_health'].side_effect = OSError('health')
        with self.assertRaises(OSError):
            self.run_migration()
        self.client.profiles.active.return_value = {'identity': {'user_id': 7}}
        self.mocks['accept_enrollment'].side_effect = migration.MigrationError('cannot switch employees')
        with self.assertRaisesRegex(migration.MigrationError, 'cannot switch employees'):
            self.run_migration()
        self.mocks['replace_current_user'].assert_not_called()

    def test_resume_rejects_changed_local_metadata(self):
        self.mocks['accept_enrollment'].side_effect = OSError('offline')
        with self.assertRaises(OSError):
            self.run_migration()
        self.mocks['discover_legacy'].return_value = {'company_id': 2}
        with self.assertRaisesRegex(migration.MigrationError, 'not a resumable'):
            self.run_migration()

    def test_resume_preserves_incomplete_installation_and_identity(self):
        self.mocks['accept_enrollment'].side_effect = [OSError('offline'), None]
        with self.assertRaises(OSError):
            self.run_migration()
        partial = self.root / 'install'
        partial.mkdir()
        (partial / 'partial.txt').write_text('retained')
        (self.root / 'identity-fixture').write_text('retained')
        self.run_migration()
        backups = list(self.root.glob('incomplete-install-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'partial.txt').read_text(), 'retained')
        self.assertEqual((self.root / 'identity-fixture').read_text(), 'retained')


@unittest.skipUnless(os.name == 'nt', 'Windows safeguards')
class ScopeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        self.exe = self.profile / 'soft_agent_windows.exe'
        self.exe.write_bytes(b'test')
        self.process = Mock(pid=123)
        self.process.name.return_value = self.exe.name
        self.process.exe.return_value = str(self.exe)
        self.process.cmdline.return_value = [str(self.exe)]
        self.process.username.return_value = 'DOMAIN\\Employee'
        import winreg
        win32ts = Mock()
        patcher = patch.dict(sys.modules, win32ts=win32ts)
        patcher.start()
        self.addCleanup(patcher.stop)
        for patcher in (patch.object(winreg, 'OpenKey', side_effect=FileNotFoundError),
                        patch.object(win32ts, 'ProcessIdToSessionId', return_value=4),
                        patch.object(migration.psutil, 'process_iter', return_value=[self.process])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_other_user_is_ignored(self):
        self.process.username.return_value = 'DOMAIN\\Other'
        result = migration.snapshot_legacy(self.profile, 'domain\\employee', 4)
        self.assertEqual(result['files'], [])
        self.process.exe.assert_not_called()

    def test_other_session_same_user_blocks(self):
        with self.assertRaisesRegex(ValueError, 'another session'):
            migration.snapshot_legacy(self.profile, 'domain\\employee', 5)

    def test_shared_binary_blocks(self):
        with self.assertRaisesRegex(ValueError, 'outside current'):
            migration.snapshot_legacy(self.profile / 'another-profile', 'domain\\employee', 4)

    def test_unknown_process_owner_blocks(self):
        self.process.username.side_effect = migration.psutil.AccessDenied(123)
        with self.assertRaises(migration.psutil.AccessDenied):
            migration.snapshot_legacy(self.profile, 'domain\\employee', 4)

    def test_custom_config_arguments_block_discovery(self):
        self.process.cmdline.return_value = [str(self.exe), '--config', 'different.json']
        with self.assertRaisesRegex(migration.MigrationError, 'Custom Legacy'):
            migration.snapshot_legacy(self.profile, 'domain\\employee', 4)

    def test_rollback_collision_blocks(self):
        self.exe.with_name(self.exe.name + '.legacy-disabled').write_bytes(b'previous')
        with self.assertRaisesRegex(ValueError, 'already disabled'):
            migration.snapshot_legacy(self.profile, 'domain\\employee', 4)


class CapabilityTests(unittest.TestCase):
    def test_filename_and_bundle_target_are_bound(self):
        token = 'a' * 64
        with patch.object(migration, 'read_json', return_value={'target': 'windows-x64-modern'}):
            self.assertEqual(migration.capability('SOFT-Tracking-Migrate-windows-x64-modern_' + token + '.exe', Path('.')), token)
            self.assertEqual(migration.capability('SOFT-Tracking-Migrate-windows-x64-modern_' + token + ' (1).exe', Path('.')), token)
            for filename in ('SOFT-Tracking-Migrate.exe',
                             'SOFT-Tracking-Migrate-windows-x86-legacy_' + token + '.exe',
                             'SOFT-Tracking-Migrate-windows-x64-modern_' + token.upper() + '.exe'):
                with self.assertRaises(migration.MigrationError):
                    migration.capability(filename, Path('.'))

    def test_redeem_retry_preserves_device_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            http = Mock()
            calls = []
            def response(path, payload):
                calls.append(payload.copy())
                if len(calls) == 1:
                    raise OSError('offline')
                return dict(ok=True, company_code='a' * 32, company_id=1, user_id=7,
                            company_name='Fixture', user_name='Fixture',
                            device_id=payload['device_id'], expires_at=int(migration.time.time()) + 300)
            http.post_json.side_effect = response
            with patch.object(migration, 'HttpClient', return_value=http):
                with self.assertRaises(OSError):
                    migration.approved_credentials(Path(temporary), {'company_id': 1}, 'c' * 64)
                self.assertEqual(migration.approved_credentials(Path(temporary), {'company_id': 1}, 'c' * 64)['user_id'], 7)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(len(calls[0]['device_secret']), 64)
            self.assertNotEqual(calls[0]['device_secret'], 'c' * 64)


class DirectEnrollmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.client = migration.Client(Path(temporary.name))
        self.addCleanup(self.client.close)
        self.addCleanup(self.client.http.session.close)
        self.redeemed = dict(ok=True, company_code='a' * 32, device_id=self.client.device['device_id'],
                             company_id=32, user_id=7, company_name='Fixture', user_name='Fixture')
        identity = self.client._enrollment_identity(self.redeemed, self.client.device)
        self.response = dict(ok=True, device_id=self.client.device['device_id'], identity=identity,
                             config={'policy_expires_at': int(migration.time.time()) + 300, 'tracking': False})

    def test_commits_identity_only_after_authenticated_config(self):
        def config(profile, path, payload):
            self.assertIsNone(self.client.state.get('identity'))
            self.assertEqual(path, '/client/v3/config')
            self.assertEqual(profile['device'], self.client.device)
            return self.response
        with patch.object(self.client, '_post', side_effect=config):
            migration.accept_enrollment(self.client, self.redeemed)
        self.assertEqual(self.client.profiles.active()['identity']['company_id'], 32)
        self.assertFalse(self.client.policy()['tracking'])
        self.assertEqual(self.client.state.get('company_code_hash'), migration.hashlib.sha256(b'a' * 32).hexdigest())

    def test_config_outage_does_not_persist_identity_and_retry_keeps_device(self):
        device = self.client.device.copy()
        with patch.object(self.client, '_post', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                migration.accept_enrollment(self.client, self.redeemed)
        self.assertIsNone(self.client.state.get('identity'))
        self.assertEqual(self.client.device, device)
        with patch.object(self.client, '_post', return_value=self.response):
            migration.accept_enrollment(self.client, self.redeemed)
            migration.accept_enrollment(self.client, self.redeemed)
        self.assertEqual(self.client.device, device)

    def test_mismatching_config_identity_never_commits(self):
        self.response['identity']['user_id'] = 8
        with patch.object(self.client, '_post', return_value=self.response):
            with self.assertRaises(migration.MigrationError):
                migration.accept_enrollment(self.client, self.redeemed)
        self.assertIsNone(self.client.state.get('identity'))

    def test_expired_policy_never_commits(self):
        self.response['config']['policy_expires_at'] = 1
        with patch.object(self.client, '_post', return_value=self.response):
            with self.assertRaises(migration.MigrationError):
                migration.accept_enrollment(self.client, self.redeemed)
        self.assertIsNone(self.client.state.get('identity'))

    def test_existing_employee_cannot_be_switched_by_new_grant(self):
        with patch.object(self.client, '_post', return_value=self.response):
            migration.accept_enrollment(self.client, self.redeemed)
            with self.assertRaisesRegex(migration.MigrationError, 'cannot switch employees'):
                migration.accept_enrollment(self.client, dict(self.redeemed, user_id=8))
        self.assertEqual(self.client.state.get('identity')['user_id'], 7)

    def test_redeem_rejects_malformed_or_wrong_device_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            http = Mock()
            http.post_json.return_value = dict(ok=True, company_code='a' * 32, company_id=1, user_id=7,
                                               device_id='wrong', expires_at=int(migration.time.time()) + 300)
            with patch.object(migration, 'HttpClient', return_value=http):
                with self.assertRaises(migration.MigrationError):
                    migration.approved_credentials(Path(temporary), {'company_id': 1}, 'c' * 64)


if __name__ == '__main__':
    unittest.main()
