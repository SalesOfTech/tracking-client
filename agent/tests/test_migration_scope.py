import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import migrate_legacy as migration
from agent_tracker import migration_scope as scope


class RestartDecisionTests(unittest.TestCase):
    def test_wrapper_arguments_and_global_softagent_name_block(self):
        for command in ('cmd /c "C:\\Program Files\\SOFT\\soft_agent_windows.exe"',
                        'SOFTAgent powershell -File C:\\managed\\start.ps1'):
            with patch.object(scope, 'global_restart_commands', return_value=[command]):
                with self.assertRaisesRegex(ValueError, 'administrator review'):
                    scope.assert_no_global_restart([Path('soft_agent_windows.exe')])

    def test_inventory_denied_is_not_treated_as_absent(self):
        with patch.object(scope, 'global_restart_commands', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
                scope.assert_no_global_restart([Path('soft_agent_windows.exe')])

    def test_unrelated_global_commands_are_not_removed_or_blocked(self):
        with patch.object(scope, 'global_restart_commands', return_value=['unrelated.exe', 'svchost.exe -k local']):
            scope.assert_no_global_restart([Path('soft_agent_windows.exe')])


@unittest.skipUnless(os.name == 'nt', 'Windows read-only startup discovery')
class RestartInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        import winreg
        from win32com import client
        from win32com.shell import shell
        self.registry = winreg
        self.folder = Mock()
        self.folder.GetTasks.return_value = []
        self.folder.GetFolders.return_value = []
        self.scheduler = Mock()
        self.scheduler.GetFolder.return_value = self.folder
        for patcher in (patch.object(winreg, 'OpenKey', side_effect=FileNotFoundError),
                        patch.object(scope.psutil, 'win_service_iter', return_value=[]),
                        patch.object(shell, 'SHGetFolderPath', return_value=str(self.root)),
                        patch.object(client, 'Dispatch', return_value=self.scheduler)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def check(self):
        scope.assert_no_global_restart([Path('soft_agent_windows.exe')])

    def test_hklm_run_blocks(self):
        key = Mock()
        key.__enter__ = Mock(return_value=key)
        key.__exit__ = Mock(return_value=False)
        with patch.object(self.registry, 'OpenKey', return_value=key), \
                patch.object(self.registry, 'QueryInfoKey', return_value=(0, 1, 0)), \
                patch.object(self.registry, 'EnumValue', return_value=('agent', 'soft_agent_windows.exe', self.registry.REG_SZ)):
            with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
                self.check()

    def test_running_disabled_service_still_blocks(self):
        service = Mock()
        service.as_dict.return_value = {'start_type': 'disabled', 'status': 'running',
                                       'name': 'Agent', 'binpath': 'soft_agent_windows.exe'}
        with patch.object(scope.psutil, 'win_service_iter', return_value=[service]):
            with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
                self.check()

    def test_stopped_demand_service_still_blocks(self):
        service = Mock()
        service.as_dict.return_value = {'start_type': 'manual', 'status': 'stopped',
                                       'name': 'Agent', 'binpath': 'soft_agent_windows.exe'}
        with patch.object(scope.psutil, 'win_service_iter', return_value=[service]):
            with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
                self.check()

    def test_disabled_stopped_service_is_inert(self):
        service = Mock()
        service.as_dict.return_value = {'start_type': 'disabled', 'status': 'stopped',
                                       'name': 'Agent', 'binpath': 'soft_agent_windows.exe'}
        with patch.object(scope.psutil, 'win_service_iter', return_value=[service]):
            self.check()

    def test_scheduled_restart_blocks(self):
        action = Mock(Type=0, Path='powershell.exe', Arguments='start soft_agent_windows.exe')
        task = Mock(Enabled=True)
        task.Definition.Actions = [action]
        self.folder.GetTasks.return_value = [task]
        with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
            self.check()

    def test_scheduler_permission_error_blocks(self):
        self.folder.GetTasks.side_effect = PermissionError('denied')
        with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
            self.check()

    def test_global_startup_script_is_not_executed(self):
        (self.root / 'start.cmd').write_text('echo fixture')
        with self.assertRaisesRegex(ValueError, 'Cannot exclude'):
            self.check()


@unittest.skipUnless(os.name == 'nt', 'Windows rollback')
class SharedRollbackTests(unittest.TestCase):
    def test_shared_files_and_disabled_sibling_hashes_unchanged_on_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exe = root / 'soft_agent_windows.exe'
            config = root / 'agent_config.json'
            disabled = root / 'soft_agent_windows.exe.legacy-disabled'
            for index, path in enumerate((exe, config, disabled)):
                path.write_bytes(('unchanged-%d' % index).encode())
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (exe, config, disabled)}
            snapshot = dict(files=[str(exe)], startup=None, running=[str(exe)])
            owner = Mock()
            owner.username.return_value = 'DOMAIN\\Employee'
            other = Mock(pid=202)
            other.username.return_value = 'DOMAIN\\Employee'
            other.exe.return_value = str(exe)
            import win32ts
            with patch.object(migration.psutil, 'Process', return_value=owner), \
                    patch.object(migration.psutil, 'process_iter', return_value=[other]), \
                    patch.object(win32ts, 'ProcessIdToSessionId', side_effect=lambda pid: 5 if pid == 202 else 4), \
                    patch.object(migration.subprocess, 'Popen') as launch, \
                    patch.object(Path, 'rename', side_effect=AssertionError('No file mutation')):
                migration.restore_legacy(snapshot, session_id=4)
                launch.assert_called_once_with([str(exe)], cwd=str(root))
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})
            other.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
