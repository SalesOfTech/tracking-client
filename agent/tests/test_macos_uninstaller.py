import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import macos_uninstall as mac, uninstaller


class MacUninstallContractTests(unittest.TestCase):
    def test_dialog_uses_literal_arguments_cancel_default_and_no_authorization(self):
        with patch.object(mac.subprocess, 'run', return_value=Mock(stdout='cancel\n')) as run:
            self.assertFalse(mac.dialog('title "quoted"', 'message\nline', 'Cancel', 'Uninstall'))
        args = run.call_args.args[0]
        self.assertEqual(args[0], '/usr/bin/osascript')
        self.assertEqual(args[3:], ['--', 'title "quoted"', 'message\nline', 'Cancel', 'Uninstall'])
        self.assertIn('default button 1 cancel button 1', args[2])
        self.assertNotIn('title "quoted"', args[2])
        self.assertNotIn('administrator privileges', args[2])
        self.assertNotIn('do shell script', args[2])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['timeout'], 300)
        with patch.object(mac.subprocess, 'run', return_value=Mock(stdout='remove\n')):
            self.assertTrue(mac.dialog('title', 'message', 'Cancel', 'Uninstall'))

    def test_foreign_owner_mount_hardlink_system_flag_and_special_file_rejected(self):
        info = dict(st_uid=501, st_dev=1, st_mode=stat.S_IFREG | 0o400, st_nlink=1, st_flags=0)
        for update in ({'st_uid': 0}, {'st_dev': 2}, {'st_nlink': 2},
                       {'st_flags': mac.SYSTEM_FLAGS}, {'st_mode': stat.S_IFIFO}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                mac._check(SimpleNamespace(**dict(info, **update)), 501, 1)
        mac._check(SimpleNamespace(**info), 501, 1)

    def test_never_repairs_foreign_or_system_owner_permissions(self):
        with patch.object(mac.os, 'fchmod', create=True) as chmod, patch.object(mac, '_set_flags') as flags:
            with self.assertRaises(ValueError):
                mac._check(SimpleNamespace(st_uid=0, st_dev=1), 501, 1)
            chmod.assert_not_called()
            flags.assert_not_called()

    def test_only_owner_mutable_flags_and_directory_bits_are_repaired(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR | 0o500, st_flags=mac.USER_FLAGS | 0x8000)
        with patch.object(mac.os, 'fchmod', create=True) as chmod, patch.object(mac, '_set_flags') as flags:
            mac._repair(42, info)
        flags.assert_called_once_with(42, 0x8000)
        chmod.assert_called_once_with(42, 0o700)

    def test_darwin_flags_use_the_checked_descriptor_and_report_system_failure(self):
        with patch.object(mac.ctypes, 'CDLL') as library:
            function = library.return_value.fchflags
            function.return_value = 0
            mac._set_flags(42, 0x8000)
            library.assert_called_once_with(None, use_errno=True)
            function.assert_called_once_with(42, 0x8000)
            self.assertEqual(function.argtypes, [mac.ctypes.c_int, mac.ctypes.c_uint])
            self.assertEqual(function.restype, mac.ctypes.c_int)
            function.return_value = -1
            with patch.object(mac.ctypes, 'get_errno', return_value=1):
                with self.assertRaises(OSError) as error:
                    mac._set_flags(42, 0)
            self.assertEqual(error.exception.errno, 1)

    def test_only_exact_registration_content_is_owned(self):
        root = Path('/Users/fixture/Library/Application Support/SOFT/TrackingV3/install')
        path = Path('/Users/fixture/Library/LaunchAgents/com.soft.tracking.v3.plist')
        value = {'Label': mac.LABEL, 'ProgramArguments': [str(root / 'soft-tracking'), '--autostart']}
        with patch.object(mac, '_read', return_value=(plistlib.dumps(value), (1, 2))):
            self.assertTrue(mac.owned_registration(path, root))
        for update in ({'Label': 'other'}, {'Program': '/Applications/Other.app/Contents/MacOS/other'},
                       {'ProgramArguments': ['/Users/other/soft-tracking', '--autostart']}):
            with patch.object(mac, '_read', return_value=(plistlib.dumps(dict(value, **update)), (1, 2))):
                self.assertFalse(mac.owned_registration(path, root))
        from agent_tracker.native_host import host_manifest
        path = Path('/Users/fixture/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.soft.tracking.json')
        for executable, expected in ((root / 'soft-tracking-host', True), (Path('/other/host'), False)):
            with patch.object(mac, '_read', return_value=(json.dumps(host_manifest(executable)).encode(), (1, 2))):
                self.assertEqual(mac.owned_registration(path, root), expected)

    def test_native_host_shutdown_never_targets_foreign_processes(self):
        import psutil
        root = Path('/Users/fixture/Library/Application Support/SOFT/TrackingV3/install')
        own = Mock(pid=101)
        own.uids.return_value = SimpleNamespace(real=501, effective=501)
        own.exe.return_value = str(root / 'versions/1.2.3/app/soft-tracking-host-app')
        foreign = Mock(pid=102)
        foreign.uids.return_value = SimpleNamespace(real=502, effective=502)
        browser = Mock(pid=103)
        browser.uids.return_value = SimpleNamespace(real=501, effective=501)
        browser.exe.return_value = '/Applications/Browser.app/Contents/MacOS/browser'
        with patch.object(mac, 'current_user', return_value=(Path('/Users/fixture'), 501)), \
                patch.object(psutil, 'process_iter', return_value=[own, foreign, browser]), \
                patch.object(psutil, 'wait_procs', return_value=([], [own])) as wait:
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                mac.stop_native_hosts(root)
        own.terminate.assert_called_once_with()
        foreign.terminate.assert_not_called()
        browser.terminate.assert_not_called()
        wait.assert_called_once_with([own], timeout=5)

    def test_engine_native_roles_are_stopped_but_other_roles_and_owners_are_not(self):
        import psutil
        root = Path('/Users/fixture/Library/Application Support/SOFT/TrackingV3/install')
        cache = '.engine-' + 'a' * 64
        def process(pid, wrapper, role, uid=501):
            executable = wrapper.parent / cache / 'SOFT Tracking Engine.app/Contents/MacOS/soft-tracking-engine'
            value = Mock(pid=pid)
            value.uids.return_value = SimpleNamespace(real=uid, effective=uid)
            value.exe.return_value = str(executable)
            value.cmdline.return_value = [str(executable), '--tracking-role', role,
                                         '--tracking-wrapper', str(wrapper), '--']
            return value
        bootstrap = process(101, root / 'soft-tracking-host', 'bootstrap-native')
        native = process(102, root / 'versions/1.2.3/app/soft-tracking-host-app', 'native')
        runtime = process(103, root / 'versions/1.2.3/app/soft-tracking-app', 'app')
        foreign = process(104, root / 'soft-tracking-host', 'bootstrap-native', uid=502)
        own = process(os.getpid(), root / 'versions/1.2.3/app/soft-tracking-app', 'app')
        with patch.object(mac, 'current_user', return_value=(Path('/Users/fixture'), 501)), \
                patch.object(mac, 'bootloader_ancestors', return_value=set()), \
                patch.object(psutil, 'process_iter', return_value=[bootstrap, native, runtime, foreign, own]), \
                patch.object(psutil, 'wait_procs', return_value=([], [runtime])) as wait:
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                mac.stop_native_hosts(root)
        bootstrap.terminate.assert_called_once_with()
        native.terminate.assert_called_once_with()
        runtime.terminate.assert_not_called()
        foreign.terminate.assert_not_called()
        own.terminate.assert_not_called()
        wait.assert_called_once_with([bootstrap, native, runtime], timeout=5)

    def test_engine_native_identity_requires_matching_cache_wrapper_and_role(self):
        root = Path('/Users/fixture/install')
        wrapper = root / 'versions/1.2.3/app/soft-tracking-host-app'
        executable = wrapper.parent / ('.engine-' + 'a' * 64) / 'SOFT Tracking Engine.app/Contents/MacOS/soft-tracking-engine'
        args = [str(executable), '--tracking-role', 'native', '--tracking-wrapper', str(wrapper), '--']
        self.assertTrue(mac.native_engine_process(root, executable, args))
        for index, value in ((0, '/other/engine'), (1, '--other'), (2, 'app'), (2, 'bootstrap-native'),
                             (3, '--other'), (4, '/other/soft-tracking-host-app'),
                             (4, str(root / 'versions/2.0.0/app/soft-tracking-host-app')),
                             (4, 'soft-tracking-host-app'), (5, '--other')):
            changed = list(args)
            changed[index] = value
            with self.subTest(index=index, value=value):
                self.assertFalse(mac.native_engine_process(root, executable, changed))
        self.assertFalse(mac.native_engine_process(root, executable, args[:5]))
        for bad in ('versions/not-a-version/app', 'versions/../app'):
            bad_wrapper = root / bad / 'soft-tracking-host-app'
            bad_executable = bad_wrapper.parent / executable.relative_to(wrapper.parent)
            self.assertFalse(mac.native_engine_process(root, bad_executable,
                [str(bad_executable), '--tracking-role', 'native', '--tracking-wrapper', str(bad_wrapper), '--']))
        for bad_cache in ('.engine-short', '.engine-' + 'A' * 64, '.engine-' + 'a' * 64 + '.tmp'):
            bad_executable = wrapper.parent / bad_cache / 'SOFT Tracking Engine.app/Contents/MacOS/soft-tracking-engine'
            self.assertFalse(mac.native_engine_process(root, bad_executable, [str(bad_executable), *args[1:]]))

    def test_only_verified_onefile_parent_is_exempt_not_same_exe_agent(self):
        import psutil
        root = Path('/Users/fixture/Library/Application Support/SOFT/TrackingV3/install')
        executable = str(root / 'versions/1.2.3/app/soft-tracking-app')
        args = ['--uninstall', '--installed-root', str(root)]
        parent, agent = Mock(pid=127), Mock(pid=126)
        for process in (parent, agent):
            process.exe.return_value = executable
            process.uids.return_value = SimpleNamespace(real=501, effective=501)
        parent.cmdline.return_value = [executable, *args]
        agent.cmdline.return_value = [executable, '--autostart']
        with patch.object(mac.sys, 'frozen', True, create=True), \
                patch.object(mac.sys, '_MEIPASS', '/private/tmp/_MEI0000007fABC123', create=True), \
                patch.object(mac.sys, 'executable', executable), patch.object(mac.sys, 'argv', [executable, *args]), \
                patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=501)), \
                patch.object(psutil, 'Process') as current, \
                patch.object(mac, 'current_user', return_value=(root.parents[4], 501)), \
                patch.object(psutil, 'process_iter', return_value=[parent, agent]), \
                patch.object(psutil, 'wait_procs', return_value=([], [agent])) as wait:
            current.return_value.parents.return_value = [parent, agent]
            self.assertEqual(mac.bootloader_ancestors(501), {127})
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                mac.stop_native_hosts(root)
            wait.assert_called_once_with([agent], timeout=5)
            parent.terminate.assert_not_called()
            agent.terminate.assert_not_called()
            parent.exe.return_value = '/other/executable'
            self.assertEqual(mac.bootloader_ancestors(501), set())
            parent.exe.return_value = executable
            parent.cmdline.return_value = [executable, '--autostart']
            self.assertEqual(mac.bootloader_ancestors(501), set())
            parent.cmdline.return_value = [executable, *args]
            current.return_value.parents.return_value = [agent]
            self.assertEqual(mac.bootloader_ancestors(501), set())

    def test_onedir_same_executable_parent_is_not_a_bootloader_exemption(self):
        with patch.object(mac.sys, 'frozen', True, create=True), \
                patch.object(mac.sys, '_MEIPASS', '/installed/app/_internal', create=True):
            self.assertEqual(mac.bootloader_ancestors(501), set())


@unittest.skipIf(os.name == 'nt', 'POSIX directory-relative no-follow filesystem APIs')
class MacUninstallFilesystemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.root = self.home / mac.WORKSPACE / 'install'
        self.payload = self.root / 'versions/1.2.3/app'
        self.payload.mkdir(parents=True)
        self.uid = os.getuid()
        patcher = patch.object(mac, 'current_user', return_value=(self.home, self.uid))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fixed_account_path_ignores_home_and_install_environment(self):
        with patch.dict(os.environ, HOME=str(self.base / 'other'), SOFT_TRACKING_INSTALL=str(self.base / 'foreign')):
            self.assertEqual(mac.owned_root(self.root), self.root)
            for path in (self.root.parent, self.base / 'other' / mac.WORKSPACE / 'install',
                         Path('/Applications/SOFT Tracking.app'), self.root / '..' / 'install'):
                with self.assertRaises(ValueError):
                    mac.owned_root(path)

    def test_account_lookup_uses_pwd_not_environment_and_refuses_root_or_elevation(self):
        import pwd
        with patch.object(os, 'getuid', return_value=501), patch.object(os, 'geteuid', return_value=501), \
                patch.object(pwd, 'getpwuid', return_value=SimpleNamespace(pw_dir=str(self.home))), \
                patch.dict(os.environ, HOME=str(self.base / 'foreign')):
            # Call the original function, outside the per-test account seam.
            self.assertEqual(REAL_CURRENT_USER(), (self.home, 501))
        for real, effective in ((0, 0), (501, 0)):
            with patch.object(os, 'getuid', return_value=real), patch.object(os, 'geteuid', return_value=effective):
                with self.assertRaises(ValueError):
                    REAL_CURRENT_USER()

    def test_payload_symlinks_are_unlinked_without_touching_targets(self):
        outside = self.base / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_bytes(b'keep')
        (self.payload / 'external').symlink_to(outside, target_is_directory=True)
        (self.payload / 'dangling').symlink_to('missing')
        framework = self.payload / 'Framework/Versions/A'
        framework.mkdir(parents=True)
        (framework.parent / 'Current').symlink_to('A', target_is_directory=True)
        (framework / 'binary').write_bytes(b'fixture')
        framework.chmod(0o500)
        mac.prepare(self.root)
        mac.cleanup(self.root)
        self.assertFalse(self.root.parent.exists())
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_root_engine_framework_aliases_are_removed_without_following_targets(self):
        cache = self.root / ('.engine-' + 'a' * 64)
        contents = cache / 'SOFT Tracking Engine.app/Contents'
        framework = contents / 'Frameworks/Python.framework/Versions/A'
        framework.mkdir(parents=True)
        (framework / 'Python').write_bytes(b'engine fixture')
        (framework.parent / 'Current').symlink_to('A', target_is_directory=True)
        (framework.parent.parent / 'Python').symlink_to('Versions/Current/Python')
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'keep').write_bytes(b'keep')
        (contents / 'external').symlink_to(outside, target_is_directory=True)
        (contents / 'dangling').symlink_to('missing')
        mac.prepare(self.root)
        mac.cleanup(self.root)
        self.assertFalse(self.root.parent.exists())
        self.assertEqual((outside / 'keep').read_bytes(), b'keep')

    def test_root_engine_link_allowance_does_not_admit_redirected_cache_or_controls(self):
        cache = self.root / ('.engine-' + 'a' * 64)
        outside = self.base / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep'
        sentinel.write_bytes(b'keep')
        paths = [cache, cache / 'SOFT Tracking Engine.app', cache / 'SOFT Tracking Engine.app/Contents',
                 cache / 'control', self.root / '.engine-invalid/SOFT Tracking Engine.app/Contents/alias',
                 self.root / ('engine-' + 'a' * 64 + '.zip')]
        for path in paths:
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(outside, target_is_directory=True)
                try:
                    with patch.object(mac, '_repair') as repair, self.assertRaises(ValueError):
                        mac.prepare(self.root)
                    repair.assert_not_called()
                finally:
                    path.unlink()
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_control_symlink_rejected_before_any_write_or_repair(self):
        target = self.base / 'outside-lock'
        target.write_bytes(b'keep')
        (self.root / 'install.lock').symlink_to(target)
        with patch.object(mac, '_repair') as repair:
            with self.assertRaises(ValueError):
                mac.prepare(self.root)
            repair.assert_not_called()
        self.assertEqual(target.read_bytes(), b'keep')

    def test_parent_symlink_and_workspace_hardlink_are_rejected(self):
        moved = self.root.with_name('original')
        self.root.rename(moved)
        self.root.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            mac.owned_root(self.root)
        self.root.unlink()
        moved.rename(self.root)
        original = self.base / 'outside-file'
        original.write_bytes(b'keep')
        os.link(original, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            mac.prepare(self.root)
        self.assertEqual(original.read_bytes(), b'keep')

    def test_symlink_swap_during_traversal_cannot_delete_outside_tree(self):
        victim = self.payload / 'victim'
        victim.mkdir()
        (victim / 'remove').touch()
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'keep').write_bytes(b'keep')
        original = mac._unchanged
        def swap(parent, name, before):
            if name == 'victim':
                victim.rename(victim.with_name('moved'))
                victim.symlink_to(outside, target_is_directory=True)
            original(parent, name, before)
        with patch.object(mac, '_unchanged', side_effect=swap):
            with self.assertRaises(ValueError):
                mac.cleanup(self.root)
        self.assertEqual((outside / 'keep').read_bytes(), b'keep')

    def test_foreign_wrapper_and_native_manifest_are_preserved(self):
        wrapper = self.home / mac.WRAPPER
        wrapper.mkdir(parents=True)
        (wrapper / 'unrelated').write_bytes(b'keep')
        host = mac.registration_paths()[0]
        host.parent.mkdir(parents=True)
        host.write_text('{"name":"com.soft.tracking","path":"/other/host"}')
        with patch.object(mac.subprocess, 'run') as command:
            mac.remove_registrations(self.root)
            command.assert_not_called()
        mac.cleanup(self.root)
        self.assertEqual((wrapper / 'unrelated').read_bytes(), b'keep')
        self.assertTrue(host.exists())

    @unittest.skipUnless(sys.platform == 'darwin', 'real macOS user flags and frozen smoke body')
    def test_real_native_isolated_home_cleanup(self):
        from agent_tracker.uninstaller_smoke import run
        self.assertEqual(run(), 0)

    @unittest.skipUnless(sys.platform == 'darwin', 'native AppleScript syntax verification')
    def test_native_dialog_scripts_compile_without_display_or_authorization(self):
        for args in (('title', 'message'), ('title', 'message', 'Cancel', 'Uninstall')):
            with patch.object(mac.subprocess, 'run', return_value=Mock(stdout='cancel')) as call:
                mac.dialog(*args)
            script = call.call_args.args[0][2]
            result = subprocess.run(['/usr/bin/osacompile', '-o', str(self.base / 'dialog.scpt'), '-e', script],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)


REAL_CURRENT_USER = mac.current_user


if __name__ == '__main__':
    unittest.main()
