import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_tracker import automatic_migration as migration
from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.electron_desktop import Controller


class AutomaticMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / 'v3'
        self.bundle = Path(temporary.name).resolve() / 'bundle'
        self.snapshot = dict(root=str(self.root), owner='fixture', metadata=dict(
            company_id=1, install_id='9e2d3d72-8594-4d1e-b7a7-99dbbe937d61',
            username='fixture', machine='fixture-pc', os='windows'))
        self.reply = dict(ok=True, company_code='a' * 32, company_id=1, user_id=7)
        self.launcher = self.root / 'install' / 'launcher'
        self.mocks = {}
        for target, names in ((migration, ('workspace', 'credentials', 'accept_enrollment', 'Client', 'protect_workspace', 'autostart')),
                              (migration.legacy_discovery, ('discover', 'revalidate', 'retire', 'restore')),
                              (migration.installer, ('install', 'installed_health', 'register_host', 'shortcuts', 'register_uninstaller', 'resolve_company')),
                              (migration.subprocess, ('Popen',))):
            for name in names:
                patcher = patch.object(target, name)
                self.mocks[name] = patcher.start()
                self.addCleanup(patcher.stop)
        self.mocks['workspace'].return_value = self.root
        self.mocks['discover'].return_value = self.snapshot
        self.mocks['credentials'].return_value = self.reply
        self.mocks['resolve_company'].return_value = 1
        def install(*args, **kwargs):
            atomic_json(self.root / 'install/current.json', {'version': '3.4.0'})
            return self.launcher
        self.mocks['install'].side_effect = install

    def run_setup(self):
        return migration.try_migrate(self.bundle, 'en')

    def journal(self):
        return read_json(self.root / migration.JOURNAL)

    def test_no_legacy_means_normal_setup_without_creating_identity(self):
        self.mocks['discover'].return_value = None
        self.assertIsNone(self.run_setup())
        self.mocks['credentials'].assert_not_called()
        self.assertFalse(self.root.exists())

    def test_existing_v3_is_not_reassigned(self):
        atomic_json(self.root / 'install/current.json', {'version': '3.3.2'})
        self.assertIsNone(self.run_setup())
        self.mocks['discover'].assert_not_called()
        self.mocks['credentials'].assert_not_called()

    def test_success_health_and_identity_precede_retirement(self):
        sequence = []
        for name in ('credentials', 'accept_enrollment', 'installed_health', 'retire', 'Popen'):
            def operation(*args, _name=name, **kwargs):
                sequence.append(_name)
                return self.reply if _name == 'credentials' else None
            self.mocks[name].side_effect = operation
        self.assertEqual(self.run_setup(), (self.launcher, 'a' * 32))
        self.assertEqual(sequence, ['credentials', 'accept_enrollment', 'installed_health', 'retire', 'Popen'])
        self.assertEqual(self.journal()['state'], 'complete')
        self.mocks['install'].assert_called_once_with(self.bundle, self.root / 'install', 'a' * 32,
                                                     integrate=False, language='en', retire_legacy=False)
        self.assertNotIn('device_secret', (self.root / migration.JOURNAL).read_text())
        self.mocks['Client'].return_value.enroll.assert_not_called()

    def test_each_prepare_failure_keeps_legacy_running(self):
        for stage in ('credentials', 'install', 'accept_enrollment', 'installed_health', 'register_host'):
            with self.subTest(stage=stage):
                # Use a distinct user's workspace for each failure stage.
                self.root = self.root.parent / stage
                self.mocks['workspace'].return_value = self.root
                self.snapshot['root'] = str(self.root)
                original = self.mocks[stage].side_effect
                self.mocks[stage].side_effect = OSError('private-detail')
                with self.assertRaisesRegex(migration.installer.InstallerError, '^setup_migration_failed$'):
                    self.run_setup()
                self.mocks[stage].side_effect = original
                self.mocks['retire'].assert_not_called()
                self.mocks['Popen'].assert_not_called()
                self.assertEqual(self.journal()['state'], 'failed_before_switch')

    def test_retry_reuses_same_identity_after_health_failure(self):
        self.mocks['installed_health'].side_effect = [OSError('health'), None]
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.assertEqual(self.journal()['state'], 'failed_before_switch')
        self.run_setup()
        self.assertEqual(self.journal()['state'], 'complete')
        self.assertEqual(self.mocks['credentials'].call_count, 2)

    def test_retry_rejects_changed_legacy_binding(self):
        self.mocks['credentials'].side_effect = OSError('offline')
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.snapshot['metadata']['company_id'] = 2
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.assertEqual(self.mocks['credentials'].call_count, 1)

    def test_crash_during_retirement_restores_before_rediscovery(self):
        self.run_setup()
        journal = self.journal()
        atomic_json(self.root / migration.JOURNAL, dict(journal, state='switching'))
        with patch.object(migration.installer, 'stopped_supervisor') as stopped, \
                patch.object(migration.installer, 'installed_launcher', return_value=self.launcher):
            self.run_setup()
        stopped.assert_called_once_with(self.root / 'install')
        self.mocks['restore'].assert_called_once_with(self.snapshot, self.root / 'install')
        self.assertEqual(self.journal()['state'], 'complete')

    def test_bound_company_download_cannot_migrate_other_company(self):
        self.mocks['resolve_company'].return_value = 2
        with self.assertRaisesRegex(migration.installer.InstallerError, 'setup_company_conflict'):
            migration.try_migrate(self.bundle, company_code='b' * 32)
        self.mocks['credentials'].assert_not_called()

    def test_source_is_rechecked_before_switch(self):
        self.mocks['revalidate'].side_effect = [None, ValueError('changed')]
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.mocks['retire'].assert_not_called()

    def test_retirement_or_launch_failure_restores_legacy(self):
        for name in ('retire', 'Popen'):
            with self.subTest(name=name):
                self.mocks[name].side_effect = OSError('failure')
                with self.assertRaises(migration.installer.InstallerError):
                    self.run_setup()
                self.assertEqual(self.journal()['state'], 'rolled_back')
                self.mocks['restore'].assert_called_with(self.snapshot, self.root / 'install')
                self.mocks['autostart'].assert_called_with(self.launcher, False)
                self.mocks[name].side_effect = None

    def test_failed_restore_is_not_silently_retried(self):
        self.mocks['retire'].side_effect = OSError('failure')
        self.mocks['restore'].side_effect = OSError('recovery failure')
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.assertEqual(self.journal()['state'], 'recovery_required')
        with self.assertRaises(migration.installer.InstallerError):
            self.run_setup()
        self.assertEqual(self.mocks['credentials'].call_count, 1)


class CredentialsTests(unittest.TestCase):
    def test_automatic_enrollment_retries_with_same_device_without_master_key(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def response(path, payload):
                self.assertEqual(path, '/client/v3/migration_auto')
                calls.append(dict(payload))
                if len(calls) == 1:
                    raise OSError('offline')
                return dict(ok=True, company_code='a' * 32, company_id=1, user_id=7,
                            company_name='Fixture', user_name='Fixture',
                            device_id=payload['device_id'], expires_at=int(time.time()) + 900)
            http = Mock()
            http.post_json.side_effect = response
            with patch.object(migration, 'HttpClient', return_value=http):
                with self.assertRaises(OSError):
                    migration.credentials(Path(folder), {'company_id': 1})
                migration.credentials(Path(folder), {'company_id': 1})
            self.assertEqual(calls[0], calls[1])
            self.assertNotIn('employee_key', calls[1])
            self.assertNotIn('migration_token', calls[1])

    def test_installer_auto_probes_once_and_health_never_migrates(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(migration, 'try_migrate', return_value=None) as migrate:
            controller = Controller(bundle=Path(folder))
            controller.command('ready', {})
            controller.job.join(timeout=5)
            controller.command('ready', {})
            self.assertEqual(controller.phase, 'waiting')
            migrate.assert_called_once()
            health = Controller(bundle=Path(folder), health=True)
            health.command('ready', {})
            migrate.assert_called_once()
            self.assertIsNone(health.job)

    def test_installer_reports_completion_for_auto_migration(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(migration, 'try_migrate', return_value=(Path(folder) / 'launcher', 'a' * 32)):
            controller = Controller(bundle=Path(folder))
            controller.command('ready', {})
            controller.job.join(timeout=5)
            self.assertEqual(controller.phase, 'complete')
            self.assertEqual(controller.code, 'a' * 32)


if __name__ == '__main__':
    unittest.main()
