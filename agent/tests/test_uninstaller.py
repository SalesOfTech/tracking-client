import base64
from contextlib import nullcontext
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import bootstrap, uninstaller as uninstall


@unittest.skipUnless(os.name == 'nt', 'Windows per-user uninstaller')
class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'SOFT/TrackingV3/install'
        self.root.mkdir(parents=True)
        self.addCleanup(patch.stopall)
        patch.object(uninstall, 'known_folder', return_value=self.base).start()
        # All OS mutations are mocked; only disposable fixture files are touched.
        self.spawn = patch.object(uninstall, 'start_helper').start()
        self.remove = patch.object(uninstall, 'remove_owned_registrations').start()
        self.notify = patch.object(uninstall, 'notify_requested').start()
        self.stop = patch('agent_tracker.installer.stopped_supervisor', side_effect=lambda *_: nullcontext()).start()

    def test_exact_user_path_only_even_with_environment_override(self):
        with patch.dict(os.environ, {'SOFT_TRACKING_INSTALL': str(self.base / 'other')}):
            self.assertEqual(uninstall.owned_root(self.root), self.root)
            for path in (self.base, self.root.parent, self.base / 'other/install',
                         self.base / 'SOFT/TrackingV3-other/install'):
                with self.assertRaises(ValueError):
                    uninstall.owned_root(path)

    def test_reparse_attribute_rejected(self):
        with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_mode=0, st_file_attributes=0x400)):
            with self.assertRaisesRegex(ValueError, 'reparse'):
                uninstall.owned_root(self.root)

    def test_cancel_has_no_side_effects(self):
        self.assertEqual(uninstall.uninstall(self.root, confirm=lambda: False), 0)
        self.stop.assert_not_called()
        self.remove.assert_not_called()
        self.notify.assert_not_called()
        self.spawn.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_confirmed_request_stops_notifies_and_schedules_not_deletes(self):
        sentinel = self.root.parent / 'state.sqlite3'
        sentinel.write_bytes(b'preserved until external helper')
        calls = []
        self.notify.side_effect = lambda *_: calls.append('request')
        self.remove.side_effect = lambda *_: calls.append('registrations')
        self.spawn.side_effect = lambda *_: calls.append('helper')
        self.assertEqual(uninstall.uninstall(self.root, confirm=lambda: True), 0)
        self.assertEqual(calls, ['request', 'registrations', 'helper'])
        self.assertTrue((self.root / uninstall.MARKER).exists())
        self.assertTrue(sentinel.exists())

    def test_stop_failure_does_not_remove_or_report_request(self):
        self.stop.side_effect = RuntimeError('still running')
        with self.assertRaises(RuntimeError):
            uninstall.uninstall(self.root, confirm=lambda: True)
        self.remove.assert_not_called()
        self.notify.assert_not_called()
        self.spawn.assert_not_called()
        self.assertFalse((self.root / uninstall.MARKER).exists())

    def test_spawn_failure_is_not_success_and_clears_marker(self):
        self.spawn.side_effect = OSError('blocked')
        with self.assertRaises(OSError):
            uninstall.uninstall(self.root, confirm=lambda: True)
        self.assertFalse((self.root / uninstall.MARKER).exists())

    def test_offline_lifecycle_hook_does_not_block_uninstall(self):
        hook = Mock(side_effect=OSError('offline'))
        uninstall.uninstall(self.root, confirm=lambda: True, lifecycle_hook=hook)
        hook.assert_called_once_with(self.root)
        self.spawn.assert_called_once_with(self.root)

    def test_bootstrap_routes_uninstall_before_supervisor_and_marker(self):
        (self.root / uninstall.MARKER).touch()
        with patch.object(uninstall, 'main', return_value=7) as main, patch.object(bootstrap, 'SingleInstance') as lock:
            self.assertEqual(bootstrap.main(root=self.root, args=['--uninstall']), 7)
            main.assert_called_once_with(self.root)
            self.assertEqual(bootstrap.main(root=self.root, args=['--uninstall', '--yes']), 2)
            self.assertEqual(bootstrap.main(root=self.root, args=[]), 1)
            self.assertEqual(bootstrap.main(native=True, root=self.root, args=[]), 1)
            lock.assert_not_called()

    def test_helper_is_bounded_literal_only_current_user_no_kill_or_elevation(self):
        script = uninstall.helper_script(self.root, 123)
        self.assertIn('WaitForExit(90000)', script)
        self.assertIn('$attempt -lt 30', script)
        self.assertIn('RegistryHive]::CurrentUser', script)
        self.assertIn('ReparsePoint', script)
        self.assertIn('GetFolderPath', script)
        self.assertIn('Remove-Item -LiteralPath', script)
        self.assertNotIn('-Recurse', script)
        self.assertNotIn('Stop-Process', script)
        self.assertNotIn('RunAs', script)
        self.assertNotIn('LocalMachine', script)
        self.assertNotIn('__UNINSTALL_KEY__', script)
        self.assertLess(script.index('Remove-Owned $workspace\n'), script.index('$base.DeleteSubKey'))
        self.assertEqual(uninstall.ps_literal("a'b; $x"), "'a''b; $x'")

    def test_powershell_helper_parses_without_executing(self):
        script = uninstall.helper_script(self.root, 123)
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        command = "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')); $t=$null; $e=$null; [void][Management.Automation.Language.Parser]::ParseInput($s,[ref]$t,[ref]$e); if($e.Count){$e | Out-String; exit 1}"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == 'nt', 'Windows per-user registrations')
class RegistrationTests(unittest.TestCase):
    def test_helper_launch_has_no_shell_elevation_or_visible_window(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(uninstall, 'helper_script', return_value='throw "fixture only"'), \
                patch.object(uninstall, 'known_folder', return_value=Path(temporary)), \
                patch.object(uninstall.subprocess, 'Popen') as spawn:
            uninstall.start_helper(Path(temporary))
        arguments = spawn.call_args.args[0]
        self.assertEqual(arguments[-2], '-EncodedCommand')
        self.assertEqual(base64.b64decode(arguments[-1]).decode('utf-16-le'), 'throw "fixture only"')
        self.assertIn('Hidden', arguments)
        self.assertNotIn('shell', spawn.call_args.kwargs)
        self.assertTrue(spawn.call_args.kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)

    def test_only_exact_owned_registrations_removed_in_both_views(self):
        from agent_tracker.native_host import HOST_NAME
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'install'
            registry = MagicMock()
            registry.KEY_READ, registry.KEY_WRITE = 1, 2
            registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY = 512, 256
            registry.REG_SZ = 1
            opened = []
            key = registry.OpenKey.return_value.__enter__.return_value
            def opening(_hive, location, _reserved, access):
                opened.append((location, access))
                return registry.OpenKey.return_value
            registry.OpenKey.side_effect = opening
            def query(_key, _name):
                location = opened[-1][0]
                if location == uninstall.RUN_KEY:
                    return ('"' + str(root / 'SoftTracking.exe') + '" --autostart', 1)
                if location == 'owned-host':
                    return (str(root.parent / (HOST_NAME + '.json')), 1)
                return ('C:\\other-user\\manifest.json', 1)
            registry.QueryValueEx.side_effect = query
            with patch.object(uninstall, 'owned_root', return_value=root), \
                    patch.object(uninstall, 'known_folder', return_value=Path(temporary)), \
                    patch('agent_tracker.browser_setup.host_locations', return_value={'Chrome': 'owned-host', 'Edge': 'foreign-host'}), \
                    patch.dict(sys.modules, {'winreg': registry}):
                uninstall.remove_owned_registrations(root)
            self.assertEqual(registry.DeleteValue.call_count, 2)
            self.assertEqual(registry.DeleteKeyEx.call_count, 2)
            for call in registry.DeleteKeyEx.call_args_list:
                self.assertEqual(call.args[:2], (registry.HKEY_CURRENT_USER, 'owned-host'))
            registry.DeleteKey.assert_not_called()
            registry.DeleteValue.assert_called_with(key, 'SOFT Tracking v3')

    def test_foreign_autostart_and_manifest_are_preserved(self):
        registry = MagicMock()
        registry.KEY_READ, registry.KEY_WRITE = 1, 2
        registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY = 512, 256
        registry.REG_SZ = 1
        registry.QueryValueEx.return_value = ('C:\\other\\application.exe', 1)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(uninstall, 'owned_root', return_value=Path(temporary) / 'install'), \
                patch.object(uninstall, 'known_folder', return_value=Path(temporary)), \
                patch('agent_tracker.browser_setup.host_locations', return_value={'Chrome': 'host'}), \
                patch.dict(sys.modules, {'winreg': registry}):
            uninstall.remove_owned_registrations(Path(temporary) / 'install')
        registry.DeleteKeyEx.assert_not_called()
        registry.DeleteValue.assert_not_called()

    def test_lifecycle_attempts_activity_then_request_without_completion_claim(self):
        client = Mock()
        calls = []
        client.flush.side_effect = lambda: calls.append('activity')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'install'
            root.mkdir()
            (root.parent / 'state.sqlite3').touch()
            with patch('agent_tracker.core.client.Client', return_value=client), \
                    patch('agent_tracker.core.lifecycle.record', side_effect=lambda c, kind: calls.append(kind)), \
                    patch('agent_tracker.core.lifecycle.flush', side_effect=lambda c, **kwargs: calls.append('lifecycle')) as flush:
                uninstall.notify_requested(root)
        self.assertEqual(calls, ['activity', 'uninstall_requested', 'lifecycle'])
        flush.assert_called_once_with(client, active_only=True)
        client.close.assert_called_once_with()

    def test_uninstall_notification_prioritizes_recent_active_event_over_backlog(self):
        from agent_tracker.core.client import Client
        from agent_tracker.core.lifecycle import record
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'install'
            root.mkdir()
            client = Client(root.parent)
            client.profiles.update_active(client.employee_epoch, identity={'company_id': 32, 'user_id': 1})
            for _ in range(105):
                record(client, 'heartbeat')
            posted = []
            def post(profile, path, payload):
                self.assertEqual(profile['epoch'], client.employee_epoch)
                self.assertEqual(path, '/client/v3/lifecycle')
                posted.extend(payload['events'])
                return {'ok': True, 'accepted': [event['id'] for event in payload['events']]}
            try:
                with patch('agent_tracker.core.client.Client', return_value=client), \
                        patch.object(client, 'flush', side_effect=OSError('activity offline')), \
                        patch.object(client, 'close'), \
                        patch.object(client.profiles, 'delivery_profiles', side_effect=AssertionError('must not visit retired profiles')), \
                        patch.object(client, '_post', side_effect=post):
                    uninstall.notify_requested(root)
                self.assertEqual(len(posted), 100)
                self.assertEqual(posted[0]['kind'], 'uninstall_requested')
                remaining = client.state.db.execute('SELECT COUNT(*) FROM lifecycle_outbox').fetchone()[0]
                self.assertEqual(remaining, 6)
            finally:
                client.close()

    def test_all_dialogs_translated_and_confirmation_defaults_to_no(self):
        from agent_tracker.i18n import TEXT
        for locale in ('ru', 'en', 'cs', 'uz'):
            for key in ('uninstall_title', 'uninstall_confirm', 'uninstall_failed', 'uninstall_incomplete', 'uninstall_flush_warning'):
                self.assertTrue(TEXT[locale][key])
        with patch.object(uninstall, 'language', return_value='en'), \
                patch.object(uninstall.ctypes.windll.user32, 'MessageBoxW', return_value=7) as dialog:
            self.assertFalse(uninstall.confirm_removal(Path('C:/fixture')))
        self.assertEqual(dialog.call_args.args[-1] & 0x100, 0x100)

    def test_installed_apps_hkcu_quoted_command_and_dedicated_launcher(self):
        with tempfile.TemporaryDirectory(prefix='uninstall space ') as temporary:
            root = Path(temporary)
            source = root / 'versions/3.2.1/launcher/SoftTracking.exe'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'fixture launcher')
            (root / 'current.json').write_text('{"version":"3.2.1"}')
            registry = MagicMock()
            registry.KEY_WRITE = 2
            registry.KEY_WOW64_64KEY = 256
            with patch.object(uninstall, 'owned_root', return_value=root), patch.dict(sys.modules, {'winreg': registry}):
                uninstall.register_uninstaller(root)
            self.assertEqual((root / 'SoftTrackingUninstall.exe').read_bytes(), b'fixture launcher')
            registry.CreateKeyEx.assert_called_once_with(registry.HKEY_CURRENT_USER, uninstall.UNINSTALL_KEY, 0, 258)
            values = {call.args[1]: call.args[4] for call in registry.SetValueEx.call_args_list}
            self.assertEqual(values['UninstallString'], '"' + str(root / 'SoftTrackingUninstall.exe') + '" --uninstall')
            self.assertEqual(values['NoModify'], 1)
            self.assertNotIn('QuietUninstallString', values)


if __name__ == '__main__':
    unittest.main()
