from contextlib import nullcontext
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import bootstrap, integration


class AutostartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.launcher = self.root / 'SOFT Tracking.exe'
        self.launcher.write_bytes(b'test fixture, never execute')
        self.startup = self.root / 'startup' / 'tracking'
        startup_path = patch.object(integration, '_startup_path', return_value=self.startup)
        startup_path.start()
        self.addCleanup(startup_path.stop)

    def test_unix_registration_written_read_back_and_removed(self):
        for system in ('macos', 'linux'):
            with self.subTest(system=system), patch.object(integration, '_system', return_value=system):
                self.assertFalse(integration.autostart_status(self.launcher)['registered'])
                integration.autostart(self.launcher, True)
                self.assertTrue(integration.autostart_status(self.launcher)['registered'])
                self.assertEqual(integration.autostart_status(self.launcher)['effective'], 'unknown')
                before = self.startup.read_bytes()
                integration.autostart(self.launcher, True)
                self.assertEqual(before, self.startup.read_bytes())
                if os.name != 'nt':
                    self.assertEqual(self.startup.stat().st_mode & 0o777, 0o600)
                integration.autostart(self.launcher, False)
                integration.autostart(self.launcher, False)
                self.assertFalse(self.startup.exists())

    def test_mac_login_agent_uses_stable_launcher_and_not_keepalive(self):
        value = plistlib.loads(integration._startup_bytes('macos', '/home/user/SOFT Tracking'))
        self.assertEqual(value['ProgramArguments'], ['/home/user/SOFT Tracking', '--autostart'])
        self.assertTrue(value['RunAtLoad'])
        self.assertNotIn('KeepAlive', value)

    def test_linux_exec_escapes_field_codes_and_two_quoting_layers(self):
        value = integration._desktop_exec('/a %f/"$`\\b')
        self.assertEqual(value, '"/a %%f/' + '\\' * 2 + '"' + '\\' * 2 + '$' + '\\' * 2 + '`' + '\\' * 4 + 'b" --autostart')

    def test_disabled_and_mismatched_linux_entries_are_not_registered(self):
        with patch.object(integration, '_system', return_value='linux'):
            for suffix in ('Hidden=true\n', 'X-GNOME-Autostart-enabled=false\n', 'OnlyShowIn=KDE;\n', 'TryExec=/missing\n'):
                integration.autostart(self.launcher, True)
                self.startup.write_text(self.startup.read_text() + suffix)
                self.assertFalse(integration.autostart_status(self.launcher)['registered'])
            self.startup.write_text('[Desktop Entry]\nType=Application\nExec=/wrong\n')
            self.assertFalse(integration.autostart_status(self.launcher)['registered'])

    def test_disabled_or_wrong_mac_arguments_are_not_registered(self):
        with patch.object(integration, '_system', return_value='macos'):
            for changes in ({'Disabled': True}, {'ProgramArguments': ['/wrong']}, {'RunAtLoad': False}, {'Program': '/wrong'}):
                integration.autostart(self.launcher, True)
                value = plistlib.loads(self.startup.read_bytes())
                value.update(changes)
                self.startup.write_bytes(plistlib.dumps(value))
                self.assertFalse(integration.autostart_status(self.launcher)['registered'])

    def test_malformed_status_is_unknown_without_repairing_file(self):
        self.startup.parent.mkdir()
        self.startup.write_bytes(b'not a startup entry')
        for system in ('macos', 'linux'):
            with patch.object(integration, '_system', return_value=system):
                self.assertIsNone(integration.autostart_status(self.launcher)['registered'])
                self.assertEqual(self.startup.read_bytes(), b'not a startup entry')

    def test_truncated_xml_does_not_break_status(self):
        self.startup.parent.mkdir()
        self.startup.write_bytes(b'<?xml version="1.0"?><plist><dict>')
        with patch.object(integration, '_system', return_value='macos'):
            self.assertIsNone(integration.autostart_status(self.launcher)['registered'])

    def test_missing_launcher_and_control_characters_are_not_installed(self):
        with patch.object(integration, '_system', return_value='linux'):
            with self.assertRaises(FileNotFoundError):
                integration.autostart(self.root / 'missing', True)
            self.assertEqual(integration.autostart_status(self.root / 'missing')['error'], 'launcher_missing')
            with self.assertRaises((ValueError, OSError)):
                integration.autostart(self.root / 'injected\nExec=other', True)
        self.assertFalse(self.startup.exists())

    def test_interrupted_atomic_write_preserves_previous_registration(self):
        with patch.object(integration, '_system', return_value='linux'):
            integration.autostart(self.launcher, True)
            before = self.startup.read_bytes()
            with patch.object(integration.os, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    integration.autostart(self.launcher, True)
            self.assertEqual(before, self.startup.read_bytes())
            self.assertEqual(list(self.startup.parent.iterdir()), [self.startup])

    def test_failed_readback_is_not_reported_as_successful_install(self):
        with patch.object(integration, '_system', return_value='linux'), \
                patch.object(integration, 'autostart_status', return_value={'registered': False}):
            with self.assertRaisesRegex(OSError, 'verified'):
                integration.autostart(self.launcher, True)

    def test_windows_uses_only_current_user_registry_with_quoted_launcher(self):
        registry = Mock(HKEY_CURRENT_USER='current-user', REG_SZ=1)
        registry.CreateKey.side_effect = lambda *_: nullcontext('key')
        registry.OpenKey.side_effect = lambda *_: nullcontext('key')
        expected = '"' + str(self.launcher.resolve()) + '" --autostart'
        registry.QueryValueEx.return_value = (expected, 1)
        with patch.object(integration, '_system', return_value='windows'), patch.dict(sys.modules, winreg=registry):
            integration.autostart(self.launcher, True)
            registry.SetValueEx.assert_called_once_with('key', 'SOFT Tracking v3', 0, 1, expected)
            registry.CreateKey.assert_called_with('current-user', r'Software\Microsoft\Windows\CurrentVersion\Run')
            self.assertTrue(integration.autostart_status(self.launcher)['registered'])
            registry.QueryValueEx.return_value = ('wrong', 1)
            self.assertFalse(integration.autostart_status(self.launcher)['registered'])
            registry.QueryValueEx.side_effect = PermissionError()
            self.assertIsNone(integration.autostart_status(self.launcher)['registered'])
            registry.DeleteValue.side_effect = FileNotFoundError()
            integration.autostart(self.launcher, False)


class BootstrapStartupTests(unittest.TestCase):
    def test_duplicate_autostart_never_requests_a_window_or_starts_another_process(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(bootstrap, 'SingleInstance', side_effect=RuntimeError('already running')), \
                patch.object(bootstrap.subprocess, 'call') as call:
            self.assertEqual(bootstrap.main(root=folder, args=['--autostart']), 0)
            self.assertFalse((Path(folder) / 'show-window.json').exists())
            self.assertEqual(bootstrap.main(root=folder, args=[]), 0)
            self.assertTrue((Path(folder) / 'show-window.json').is_file())
            call.assert_not_called()

    def test_first_autostart_preserves_supervisor_argument(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(bootstrap, 'app_path', return_value=Path(folder) / 'app'), \
                patch.object(bootstrap.subprocess, 'call', return_value=0) as call:
            self.assertEqual(bootstrap.main(root=folder, args=['--autostart']), 0)
            self.assertEqual(call.call_args.args[0], [str(Path(folder) / 'app'), '--supervisor', '--installed-root', folder, '--autostart'])


if __name__ == '__main__':
    unittest.main()
