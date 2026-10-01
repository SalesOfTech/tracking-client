import builtins
from contextlib import nullcontext
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import uninstaller


class UninstallerAPITests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'install'
        self.root.mkdir()
        self.app = self.root / 'versions/1.2.3/app/SoftTrackingApp.exe'
        self.app.parent.mkdir(parents=True)
        self.app.touch()
        (self.root / 'current.json').write_text('{"version":"1.2.3"}')

    def test_launch_uses_active_payload_returns_unwaited_child_and_no_elevation(self):
        child = Mock()
        with patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(uninstaller, 'active_executable', return_value=self.app), \
                patch.object(uninstaller.subprocess, 'Popen', return_value=child) as spawn, \
                patch.dict(os.environ, SOFT_TRACKING_HEALTH='old-health', SOFT_TRACKING_RUN_TOKEN='old-token'):
            self.assertIs(uninstaller.launch_uninstaller(self.root), child)
        self.assertEqual(spawn.call_args.args[0], [str(self.app), '--uninstall', '--installed-root', str(self.root)])
        self.assertNotIn('shell', spawn.call_args.kwargs)
        self.assertNotIn('SOFT_TRACKING_HEALTH', spawn.call_args.kwargs['env'])
        self.assertNotIn('SOFT_TRACKING_RUN_TOKEN', spawn.call_args.kwargs['env'])
        self.assertEqual(spawn.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        child.wait.assert_not_called()
        child.communicate.assert_not_called()
        if os.name == 'nt':
            self.assertTrue(spawn.call_args.kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
        else:
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])

    def test_active_pointer_selects_current_not_retained_launcher(self):
        if os.name != 'nt':
            self.app = self.app.with_name('soft-tracking-app')
            self.app.touch()
            self.app.chmod(0o700)
        with patch.object(uninstaller.sys, 'platform', 'linux'):
            self.assertEqual(uninstaller.active_executable(self.root), self.app)
            (self.root / 'current.json').write_text('{"version":"../../foreign"}')
            with self.assertRaises(ValueError):
                uninstaller.active_executable(self.root)

    def test_launch_error_is_exact_sanitized_key(self):
        with patch.object(uninstaller, 'owned_root', side_effect=PermissionError('private user/path/token')):
            with self.assertRaisesRegex(ValueError, '^uninstall_failed$') as error:
                uninstaller.launch_uninstaller(self.root)
            self.assertIsNone(error.exception.__cause__)
            self.assertFalse(uninstaller.can_uninstall(self.root))

    def test_marker_and_unsupported_platform_disable_action(self):
        with patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(uninstaller, 'active_executable', return_value=self.app):
            (self.root / uninstaller.MARKER).touch()
            self.assertFalse(uninstaller.can_uninstall(self.root))
            with patch.object(uninstaller.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(ValueError, '^uninstall_failed$'):
                    uninstaller.launch_uninstaller(self.root)
                spawn.assert_not_called()
        with patch.object(uninstaller.os, 'name', 'posix'), patch.object(uninstaller.sys, 'platform', 'linux'):
            self.assertFalse(uninstaller.can_uninstall(self.root))

    def test_dispatch_uses_explicit_cancel_code_and_preserves_legacy_default(self):
        with patch.object(uninstaller, 'main', return_value=2) as main:
            self.assertEqual(uninstaller.dispatch_cli(['--uninstall', '--installed-root', str(self.root)]), 2)
            main.assert_called_once_with(self.root, cancel_code=2)
        with patch.object(uninstaller, 'uninstall', return_value=0) as remove:
            self.assertEqual(uninstaller.main(self.root), 0)
            remove.assert_called_once_with(self.root, cancel_code=0)
        self.assertIsNone(uninstaller.dispatch_cli(['--health-check']))

    def test_runtime_dispatch_happens_before_ui_import(self):
        entry = Path(__file__).resolve().parents[1] / 'runtime_entry.py'
        original = builtins.__import__
        def checked_import(name, *args, **kwargs):
            if name in ('agent_tracker.electron_desktop', 'agent_tracker.qt_desktop', 'agent_tracker.supervisor'):
                self.fail('Uninstall imported a runtime/UI entry point')
            return original(name, *args, **kwargs)
        with patch.object(sys, 'argv', [str(entry), '--uninstall', '--installed-root', str(self.root)]), \
                patch.object(uninstaller, 'main', return_value=2), patch('builtins.__import__', side_effect=checked_import):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(entry), run_name='__main__')
            self.assertEqual(result.exception.code, 2)

    def test_legacy_desktop_dispatch_happens_before_instance_lock(self):
        from agent_tracker import desktop
        with patch.object(uninstaller, 'main', return_value=2), \
                patch.object(desktop, 'SingleInstance') as lock, patch.object(desktop, 'Client') as client:
            self.assertEqual(desktop.main(['--uninstall', '--installed-root', str(self.root)]), 2)
            lock.assert_not_called()
            client.assert_not_called()

    def test_bounded_delivery_kills_and_reaps_only_its_child(self):
        child = Mock()
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture', 5), 0]
        with patch.object(uninstaller, 'active_executable', return_value=self.app), \
                patch.object(uninstaller.subprocess, 'Popen', return_value=child) as spawn:
            uninstaller.notify_isolated(self.root)
        self.assertEqual(spawn.call_args.args[0][1:], ['--uninstall-notify', '--installed-root', str(self.root)])
        self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT', spawn.call_args.kwargs['env'])
        child.kill.assert_called_once_with()
        self.assertEqual(child.wait.call_count, 2)

    def test_mac_cancel_never_prepares_stops_or_changes_registration(self):
        from agent_tracker import macos_uninstall as mac
        with patch.object(uninstaller.sys, 'platform', 'darwin'), \
                patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(mac, 'prepare') as prepare, patch.object(mac, 'cleanup') as cleanup:
            self.assertEqual(uninstaller.uninstall(self.root, confirm=lambda: False, cancel_code=2), 2)
            prepare.assert_not_called()
            cleanup.assert_not_called()

    def test_mac_stop_failure_never_notifies_or_deletes(self):
        from agent_tracker import macos_uninstall as mac
        with patch.object(uninstaller.sys, 'platform', 'darwin'), \
                patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(uninstaller, 'SingleInstance', side_effect=lambda *_: nullcontext()), \
                patch.object(mac, 'prepare'), patch.object(mac, 'cleanup') as cleanup, \
                patch.object(mac, 'remove_registrations') as registrations, \
                patch.object(uninstaller, 'notify_isolated') as notify, \
                patch('agent_tracker.installer.stopped_supervisor', side_effect=RuntimeError('alive')):
            with self.assertRaises(RuntimeError):
                uninstaller.uninstall(self.root, confirm=lambda: True)
            cleanup.assert_not_called()
            registrations.assert_not_called()
            notify.assert_not_called()
            self.assertFalse((self.root / uninstaller.MARKER).exists())

    def test_mac_surviving_delivery_child_prevents_deletion(self):
        from agent_tracker import macos_uninstall as mac
        with patch.object(uninstaller.sys, 'platform', 'darwin'), \
                patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(uninstaller, 'SingleInstance', side_effect=lambda *_: nullcontext()), \
                patch.object(mac, 'prepare'), patch.object(mac, 'cleanup') as cleanup, \
                patch.object(mac, 'remove_registrations') as registrations, \
                patch.object(mac, 'stop_native_hosts', side_effect=[None, RuntimeError('alive')]), \
                patch.object(uninstaller, 'notify_isolated') as notify, \
                patch('agent_tracker.installer.stopped_supervisor', side_effect=lambda *_: nullcontext()):
            with self.assertRaises(RuntimeError):
                uninstaller.uninstall(self.root, confirm=lambda: True)
            notify.assert_called_once_with(self.root)
            cleanup.assert_not_called()
            registrations.assert_not_called()

    def test_mac_failure_message_never_exposes_exception_or_os_output(self):
        from agent_tracker import macos_uninstall as mac
        from agent_tracker.i18n import translate
        with patch.object(uninstaller.sys, 'platform', 'darwin'), \
                patch.object(uninstaller, 'owned_root', return_value=self.root), \
                patch.object(uninstaller, 'language', return_value='en'), \
                patch.object(uninstaller, 'uninstall', side_effect=PermissionError('private path or stderr')), \
                patch.object(mac, 'dialog') as dialog:
            self.assertEqual(uninstaller.main(self.root, cancel_code=2), 1)
        dialog.assert_called_once_with(translate('en', 'uninstall_title'), translate('en', 'uninstall_failed'))


if __name__ == '__main__':
    unittest.main()
