import ctypes
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import admin_control, admin_platform
from agent_tracker.core.event_queue import EventQueue


class AdminControlTests(unittest.TestCase):
    def test_status_is_read_only_and_explicit_about_security_boundary(self):
        control = admin_control.AdminControl(Path('install'))
        with patch.object(admin_platform, 'authorization_status', return_value={'available': True}) as status, \
                patch.object(admin_control, 'autostart_status', return_value={'registered': True}), \
                patch.object(admin_platform, 'authorize_stop') as authorize:
            result = control.status()
        self.assertEqual(result['scope'], 'per_user')
        self.assertTrue(result['admin_required'])
        self.assertFalse(result['force_kill_protected'])
        self.assertFalse(result['request_pending'])
        self.assertNotIn('authorized', result)
        status.assert_called_once_with()
        authorize.assert_not_called()

    def test_uninstalled_status_does_not_invent_startup_registration(self):
        with patch.object(admin_platform, 'authorization_status', return_value={'available': False}):
            result = admin_control.AdminControl().status()
        self.assertFalse(result['autostart']['registered'])
        self.assertEqual(result['autostart']['error'], 'not_installed')

    def test_only_explicit_authorization_succeeds_and_nothing_is_cached(self):
        control = admin_control.AdminControl()
        outcomes = ['authorized', 'cancelled', 'denied', 'timed_out', 'unavailable', 'error', None, True, {'authorized': True}]
        with patch.object(admin_platform, 'authorize_stop', side_effect=outcomes) as authorize:
            for outcome in outcomes:
                result = control.request_stop()
                self.assertEqual(result['authorized'], outcome == 'authorized')
                self.assertFalse(control._request_lock.locked())
        self.assertEqual(authorize.call_count, len(outcomes))

    def test_helper_exception_is_sanitized_and_unlocks_request(self):
        with patch.object(admin_platform, 'authorize_stop', side_effect=OSError('sensitive account detail')):
            control = admin_control.AdminControl()
            self.assertEqual(control.request_stop(), {'authorized': False, 'state': 'error'})
            self.assertFalse(control._request_lock.locked())

    def test_concurrent_requests_cannot_open_multiple_prompts(self):
        control = admin_control.AdminControl()
        entered, release = threading.Event(), threading.Event()
        results = []
        def prompt():
            entered.set()
            release.wait(5)
            return 'cancelled'
        with patch.object(admin_platform, 'authorize_stop', side_effect=prompt) as authorize:
            worker = threading.Thread(target=lambda: results.append(control.request_stop()))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(control.request_stop(), {'authorized': False, 'state': 'busy'})
                authorize.assert_called_once_with()
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [{'authorized': False, 'state': 'cancelled'}])

    def test_all_outcomes_preserve_durable_queue_and_create_no_stop_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            event = {'event_id': 'a' * 32, 'type': 'navigation', 'timestamp': 1700000000}
            queue = EventQueue(root / 'outbox.sqlite3')
            queue.push_payload(event)
            queue.close()
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            control = admin_control.AdminControl(root / 'install')
            for outcome in ('cancelled', 'denied', 'timed_out', 'unavailable', 'error', 'authorized'):
                with patch.object(admin_platform, 'authorize_stop', return_value=outcome):
                    control.request_stop()
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})
            queue = EventQueue(root / 'outbox.sqlite3')
            try:
                self.assertEqual(queue.batch(), [event])
            finally:
                queue.close()


class PlatformAuthorizationTests(unittest.TestCase):
    def test_missing_helper_fails_closed_without_spawning(self):
        with patch.object(admin_platform, 'authorization_status', return_value={'available': False}), \
                patch.object(admin_platform.subprocess, 'run') as run, \
                patch.object(admin_platform, '_windows_authorize') as windows:
            self.assertEqual(admin_platform.authorize_stop(), 'unavailable')
        run.assert_not_called()
        windows.assert_not_called()

    def test_unsupported_platform_has_no_authorization(self):
        with patch.object(admin_platform, '_system', return_value='unsupported'):
            self.assertEqual(admin_platform.authorization_status(), {'available': False, 'mechanism': 'unavailable'})

    def test_status_checks_helpers_without_spawning(self):
        for system, count in (('macos', 2), ('linux', 2)):
            with self.subTest(system=system), patch.object(admin_platform, '_system', return_value=system), \
                    patch.object(admin_platform, '_trusted_unix_tool') as trusted, \
                    patch.object(admin_platform.subprocess, 'run') as run:
                self.assertTrue(admin_platform.authorization_status()['available'])
                self.assertEqual(trusted.call_count, count)
                run.assert_not_called()

    def test_untrusted_or_missing_helper_is_unavailable(self):
        with patch.object(admin_platform, '_system', return_value='linux'), \
                patch.object(admin_platform, '_trusted_unix_tool', side_effect=OSError('unsafe')):
            self.assertFalse(admin_platform.authorization_status()['available'])

    def test_unix_helper_ownership_and_writable_ancestors_are_checked(self):
        fake = Mock()
        fake.parents = [Mock()]
        fake.resolve.return_value = fake
        fake.is_file.return_value = True
        good = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o755)
        fake.lstat.return_value = good
        fake.parents[0].lstat.return_value = good
        with patch.object(admin_platform, 'Path', return_value=fake), \
                patch.object(admin_platform.os, 'access', return_value=True):
            admin_platform._trusted_unix_tool('/usr/bin/true')
            for bad in (SimpleNamespace(st_uid=1000, st_mode=stat.S_IFREG | 0o755),
                        SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o777)):
                fake.parents[0].lstat.return_value = bad
                with self.assertRaises(OSError):
                    admin_platform._trusted_unix_tool('/usr/bin/true')

    def test_linux_command_is_fixed_root_authorization_without_shell_or_tty(self):
        with patch.object(admin_platform, '_trusted_unix_tool', side_effect=lambda value: value), \
                patch.object(admin_platform.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(admin_platform._unix_authorize('linux'), 'authorized')
        command = run.call_args.args[0]
        self.assertEqual(command, ['/usr/bin/pkexec', '--disable-internal-agent', '--user', 'root', '/usr/bin/true'])
        options = run.call_args.kwargs
        self.assertNotIn('shell', options)
        self.assertEqual(options['stdin'], subprocess.DEVNULL)
        self.assertEqual(options['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        self.assertEqual(options['cwd'], '/')
        self.assertEqual(options['timeout'], 180)

    def test_macos_has_only_a_constant_administrator_operation(self):
        with patch.object(admin_platform, '_trusted_unix_tool', side_effect=lambda value: value), \
                patch.object(admin_platform.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(admin_platform._unix_authorize('macos'), 'authorized')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/osascript', '-e', admin_platform.MAC_SCRIPT])
        self.assertIn('do shell script "/usr/bin/true" with administrator privileges', admin_platform.MAC_SCRIPT)

    def test_unix_cancellation_and_failure_never_authorize(self):
        for system, code, stderr, expected in (
            ('linux', 126, b'', 'cancelled'), ('linux', 127, b'', 'denied'),
            ('linux', -9, b'', 'denied'), ('macos', 1, b'User canceled. (-128)', 'cancelled'),
            ('macos', 1, b'permission denied', 'denied')):
            with self.subTest(system=system, code=code), \
                    patch.object(admin_platform, '_trusted_unix_tool', side_effect=lambda value: value), \
                    patch.object(admin_platform.subprocess, 'run', return_value=SimpleNamespace(returncode=code, stderr=stderr)):
                self.assertEqual(admin_platform._unix_authorize(system), expected)

    def test_timeouts_and_errors_fail_closed(self):
        for error, expected in ((subprocess.TimeoutExpired('fixed helper', 180), 'timed_out'),
                                (OSError('detail'), 'error')):
            with patch.object(admin_platform, 'authorization_status', return_value={'available': True}), \
                    patch.object(admin_platform, '_system', return_value='linux'), \
                    patch.object(admin_platform, '_unix_authorize', side_effect=error):
                self.assertEqual(admin_platform.authorize_stop(), expected)

    def windows(self, launched=True, wait=0, exit_code=0, handle=42, exit_read=True):
        shell, kernel = Mock(), Mock()
        def launch(pointer):
            request = pointer._obj
            self.assertEqual(request.lpVerb, 'runas')
            self.assertEqual(request.lpFile, str(Path('C:/Windows/System32') / 'whoami.exe'))
            self.assertIsNone(request.lpParameters)
            self.assertEqual(request.lpDirectory, str(Path('C:/Windows/System32')))
            self.assertEqual(request.nShow, 0)
            self.assertEqual(request.fMask, 0x540)
            request.hProcess = handle
            return launched
        def get_exit(_handle, pointer):
            pointer._obj.value = exit_code
            return exit_read
        shell.ShellExecuteExW.side_effect = launch
        kernel.WaitForSingleObject.return_value = wait
        kernel.GetExitCodeProcess.side_effect = get_exit
        with patch.object(ctypes, 'WinDLL', side_effect=[shell, kernel], create=True), \
                patch.object(admin_platform, '_windows_directory', return_value=Path('C:/Windows/System32')):
            result = admin_platform._windows_authorize()
        return result, kernel

    def test_windows_requires_finished_successful_uac_process(self):
        result, kernel = self.windows()
        self.assertEqual(result, 'authorized')
        kernel.WaitForSingleObject.assert_called_once_with(42, 180000)
        kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_failure_paths_close_handle_and_fail_closed(self):
        for options, expected in (({'wait': 258}, 'timed_out'), ({'wait': 0xffffffff}, 'error'),
                                  ({'exit_code': 1}, 'denied'), ({'exit_read': False}, 'error')):
            with self.subTest(options=options):
                result, kernel = self.windows(**options)
                self.assertEqual(result, expected)
                kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_cancel_or_missing_handle_never_authorizes(self):
        for code, expected in ((1223, 'cancelled'), (5, 'denied')):
            with patch.object(ctypes, 'get_last_error', return_value=code, create=True):
                result, kernel = self.windows(launched=False)
                self.assertEqual(result, expected)
                kernel.WaitForSingleObject.assert_not_called()
                kernel.CloseHandle.assert_not_called()
        result, kernel = self.windows(handle=None)
        self.assertEqual(result, 'error')
        kernel.WaitForSingleObject.assert_not_called()


if __name__ == '__main__':
    unittest.main()
