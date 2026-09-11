import builtins
import os
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


class SetupEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.health = self.root / 'health.json'
        self.entry = runpy.run_path(str(Path(__file__).parents[1] / 'setup_entry.py'))
        self.addCleanup(patch.stopall)
        patch.object(sys, 'argv', ['setup.exe', '--health-check']).start()
        patch.dict(os.environ, {'SOFT_TRACKING_HEALTH': str(self.health)}).start()
        patch.object(sys, '_MEIPASS', str(self.root), create=True).start()
        self.electron = types.ModuleType('agent_tracker.electron_desktop')
        self.electron.electron_path = Mock(return_value=self.root / 'electron.exe')
        self.electron.installer_main = Mock(return_value=0)
        self.legacy = types.ModuleType('agent_tracker.installer')
        self.legacy.main = Mock(return_value=0)
        patch.dict(sys.modules, {'agent_tracker.electron_desktop': self.electron,
                                 'agent_tracker.installer': self.legacy}).start()

    def phases(self):
        return Path(str(self.health) + '.phase').read_text().splitlines()

    def test_electron_success_preserves_bundle_routing_and_result(self):
        (self.root / 'electron.exe').touch()
        self.assertEqual(self.entry['entrypoint'](), 0)
        self.electron.electron_path.assert_called_once_with(self.root / 'setup-payload' / 'electron')
        self.electron.installer_main.assert_called_once_with()
        self.legacy.main.assert_not_called()
        self.assertEqual(self.phases(), ['setup-entry', 'setup-imported', 'setup-electron'])
        self.assertFalse(Path(str(self.health) + '.error').exists())
        self.assertFalse(self.health.exists())

    def test_legacy_success_preserves_fallback(self):
        self.assertEqual(self.entry['entrypoint'](), 0)
        self.legacy.main.assert_called_once_with()
        self.electron.installer_main.assert_not_called()
        self.assertEqual(self.phases(), ['setup-entry', 'setup-imported', 'setup-legacy'])

    def test_health_failure_exits_nonzero_without_logging_message_or_locals(self):
        (self.root / 'electron.exe').touch()
        self.electron.installer_main.side_effect = RuntimeError('private-enrollment-token-do-not-log')
        self.assertEqual(self.entry['entrypoint'](), 1)
        error = Path(str(self.health) + '.error').read_text()
        self.assertTrue(error.startswith('RuntimeError\n'))
        self.assertIn('setup_entry.py:', error)
        self.assertNotIn('private-enrollment-token', error)
        self.assertNotIn(str(self.root), error)
        self.assertEqual(self.phases()[-1], 'setup-failed')
        self.assertFalse(self.health.exists())

    def test_import_failure_is_captured_before_electron_module_initializes(self):
        original = builtins.__import__
        def importing(name, *args, **kwargs):
            if name == 'agent_tracker.electron_desktop':
                raise ImportError('private-import-details')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=importing):
            self.assertEqual(self.entry['entrypoint'](), 1)
        self.assertEqual(self.phases(), ['setup-entry', 'setup-failed'])
        error = Path(str(self.health) + '.error').read_text()
        self.assertTrue(error.startswith('ImportError\n'))
        self.assertNotIn('private-import-details', error)

    def test_no_health_flag_or_path_does_not_swallow_normal_failures(self):
        for arguments, marker in [(['setup.exe'], str(self.health)), (['setup.exe', '--health-check'], '')]:
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', arguments), patch.dict(os.environ, {'SOFT_TRACKING_HEALTH': marker}):
                self.legacy.main.side_effect = RuntimeError('ordinary failure')
                with self.assertRaisesRegex(RuntimeError, 'ordinary failure'):
                    self.entry['entrypoint']()
        self.assertFalse(Path(str(self.health) + '.error').exists())
        self.assertFalse(Path(str(self.health) + '.phase').exists())

    def test_diagnostic_write_failure_still_exits_nonzero(self):
        self.legacy.main.side_effect = RuntimeError('do-not-log')
        with patch.object(Path, 'write_text', side_effect=OSError('unwritable')):
            self.assertEqual(self.entry['entrypoint'](), 1)
        self.assertEqual(self.phases()[-1], 'setup-failed')

    def test_explicit_exit_status_is_preserved(self):
        self.legacy.main.side_effect = SystemExit(7)
        with self.assertRaises(SystemExit) as error:
            self.entry['entrypoint']()
        self.assertEqual(error.exception.code, 7)
        self.assertFalse(Path(str(self.health) + '.error').exists())


if __name__ == '__main__':
    unittest.main()
