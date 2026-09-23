import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import legacy_migration as migration


@unittest.skipUnless(os.name == 'nt', 'Windows per-user migration')
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.root = self.profile / 'new'
        self.root.mkdir()
        self.exe = self.profile / 'SOFT Agent Tracking.exe'
        self.exe.write_bytes(b'fixture only')
        self.registry = Mock()
        self.key = Mock()
        self.registry.OpenKey.return_value.__enter__ = Mock(return_value=self.key)
        self.registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
        self.registry.KEY_QUERY_VALUE, self.registry.KEY_SET_VALUE = 1, 2
        self.command = '"' + str(self.exe) + '"'
        self.registry.QueryValueEx.return_value = (self.command, 1)
        self.owner = Mock()
        self.owner.username.return_value = 'DOMAIN\\Employee'
        self.process = Mock()
        self.process.name.return_value = self.exe.name
        self.process.exe.return_value = str(self.exe)
        self.process.username.return_value = 'DOMAIN\\Employee'
        self.process.pid = 100
        for patcher in (patch.dict(sys.modules, winreg=self.registry),
                        patch.object(migration.psutil, 'Process', return_value=self.owner),
                        patch.object(migration.psutil, 'process_iter', return_value=[self.process]),
                        patch.object(Path, 'home', return_value=self.profile)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_forced_replacement_keeps_config_and_rollback_binary(self):
        config = self.profile / 'config.json'
        config.write_text('unchanged')
        result = migration.replace_current_user(self.root)
        self.process.kill.assert_called_once()
        self.process.wait.assert_called_once_with(timeout=10)
        self.registry.DeleteValue.assert_called_once_with(self.key, 'SOFTAgent')
        self.assertEqual(result['state'], 'complete')
        self.assertFalse(self.exe.exists())
        self.assertTrue(self.exe.with_name(self.exe.name + '.legacy-disabled').exists())
        self.assertEqual(config.read_text(), 'unchanged')

    def test_other_user_process_is_never_terminated(self):
        self.registry.QueryValueEx.side_effect = FileNotFoundError
        self.process.username.return_value = 'DOMAIN\\SomeoneElse'
        self.assertEqual(migration.replace_current_user(self.root)['state'], 'not_found')
        self.process.kill.assert_not_called()
        self.assertTrue(self.exe.exists())

    def test_shared_executable_is_retained(self):
        with patch.object(migration, 'removable', return_value=False):
            result = migration.replace_current_user(self.root)
        self.assertTrue(self.exe.exists())
        self.assertEqual(result['retained'], [str(self.exe)])

    def test_access_denied_does_not_remove_binary(self):
        self.process.kill.side_effect = migration.psutil.AccessDenied(100)
        with self.assertRaisesRegex(ValueError, 'legacy_migration_denied'):
            migration.replace_current_user(self.root)
        self.assertTrue(self.exe.exists())

    def test_unrecognized_startup_is_left_alone(self):
        self.registry.QueryValueEx.return_value = ('C:\\Windows\\other.exe', 1)
        with self.assertRaisesRegex(ValueError, 'legacy_migration_unrecognized'):
            migration.replace_current_user(self.root)
        self.registry.DeleteValue.assert_not_called()
        self.process.kill.assert_not_called()

    def test_startup_parser_does_not_accept_arguments_or_new_agent(self):
        self.assertEqual(migration.startup_executable(self.command), self.exe)
        self.assertIsNone(migration.startup_executable(self.command + ' --arbitrary'))
        self.assertIsNone(migration.startup_executable(str(self.profile / 'SOFT Tracking.exe')))

    def test_external_path_is_not_removable(self):
        self.assertFalse(migration.removable(self.exe, self.profile / 'another-user'))


if __name__ == '__main__':
    unittest.main()
