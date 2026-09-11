import json
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import browser_health, browser_setup, native_host


PAGES = {
    'Chrome': 'chrome://extensions', 'Edge': 'edge://extensions',
    'Yandex': 'browser://extensions', 'Opera': 'opera://extensions',
    'Brave': 'brave://extensions', 'Vivaldi': 'vivaldi://extensions',
    'Chromium': 'chrome://extensions', 'Firefox': 'about:addons',
}
WINDOWS_EXECUTABLES = {
    'Chrome': 'Google/Chrome/Application/chrome.exe',
    'Edge': 'Microsoft/Edge/Application/msedge.exe',
    'Yandex': 'Yandex/YandexBrowser/Application/browser.exe',
    'Opera': 'Programs/Opera/launcher.exe',
    'Brave': 'BraveSoftware/Brave-Browser/Application/brave.exe',
    'Vivaldi': 'Vivaldi/Application/vivaldi.exe',
    'Chromium': 'Chromium/Application/chrome.exe',
    'Firefox': 'Mozilla Firefox/firefox.exe',
}
MAC_APPS = dict(zip(PAGES, ('Google Chrome.app', 'Microsoft Edge.app', 'Yandex.app',
                          'Opera.app', 'Brave Browser.app', 'Vivaldi.app', 'Chromium.app', 'Firefox.app')))


class BrowserSetupTests(unittest.TestCase):
    def setUp(self):
        self.home = Path('C:/browser-test/home')
        self.root = Path('C:/browser-test/state')
        self.install = Path('C:/browser-test/install')
        self.process = SimpleNamespace(Popen=Mock(), DEVNULL=-3, CREATE_NO_WINDOW=0x08000000)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # No test may modify real browser locations or start a real process.
        self.stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('Real launch forbidden')))
        self.stack.enter_context(patch.object(browser_setup, 'subprocess', self.process))
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.stack.enter_context(patch.object(Path, 'resolve', autospec=True,
                                             side_effect=lambda path, *args, **kwargs: path))
        self.is_file = self.stack.enter_context(patch.object(Path, 'is_file', autospec=True, return_value=False))
        self.is_dir = self.stack.enter_context(patch.object(Path, 'is_dir', autospec=True, return_value=False))
        self.mkdir = self.stack.enter_context(patch.object(Path, 'mkdir', autospec=True))
        self.write = self.stack.enter_context(patch.object(Path, 'write_text', autospec=True))
        self.which = self.stack.enter_context(patch.object(browser_setup.shutil, 'which', return_value=None))

    @contextmanager
    def operating_system(self, name, system, environ=None):
        fake_os = SimpleNamespace(name=name, environ=environ or {})
        with ExitStack() as stack:
            stack.enter_context(patch.object(browser_setup, 'os', fake_os))
            stack.enter_context(patch.object(browser_health, 'os', fake_os))
            stack.enter_context(patch.object(browser_setup.platform, 'system', return_value=system))
            yield

    def registry(self):
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_WRITE=2,
                                   KEY_WOW64_32KEY=0x200, KEY_WOW64_64KEY=0x100, REG_SZ=1,
                                   CreateKeyEx=Mock(), SetValueEx=Mock(), OpenKey=Mock(), QueryValueEx=Mock())

        def key(root, path, *args):
            context = MagicMock()
            context.__enter__.return_value = (root, path, *args)
            return context

        registry.CreateKeyEx.side_effect = key
        registry.OpenKey.side_effect = key
        return registry

    def assert_launch(self, argv, flags):
        args, kwargs = self.process.Popen.call_args
        self.assertEqual((argv,), args)
        self.assertIsInstance(args[0], list)
        self.assertIs(False, kwargs.get('shell', False))
        self.assertEqual(flags, kwargs['creationflags'])
        for stream in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(self.process.DEVNULL, kwargs[stream])

    def test_family_and_page_allowlists_are_fixed_and_reject_user_commands(self):
        self.assertEqual(tuple(PAGES), browser_setup.BROWSER_FAMILIES)
        self.assertEqual(PAGES, browser_setup.EXTENSION_PAGES)
        for family in (None, True, [], {}, '', 'chrome', 'Safari', 'Chrome --profile=secret',
                       'Chrome; calc.exe', 'https://example.test', '../Chrome'):
            with self.subTest(family=family), self.assertRaises(ValueError):
                browser_setup.open_extensions_page(family)
        self.process.Popen.assert_not_called()
        self.is_file.assert_not_called()
        self.which.assert_not_called()

    def test_windows_resolves_each_requested_family_without_default_browser(self):
        base = Path('C:/browser-test/Local App Data')
        with self.operating_system('nt', 'Windows', {'LOCALAPPDATA': str(base)}):
            for family, relative in WINDOWS_EXECUTABLES.items():
                target = base / relative
                self.is_file.side_effect = lambda path: path == target
                with self.subTest(family=family):
                    self.assertEqual(target, browser_setup.resolve_browser(family))
                    result = browser_setup.open_extensions_page(family)
                    self.assertEqual({'browser': family, 'extension_page': PAGES[family], 'opened': True}, result)
                    self.assert_launch([str(target), PAGES[family]], self.process.CREATE_NO_WINDOW)
        self.which.assert_not_called()

    def test_windows_checks_user_then_both_program_files_locations(self):
        bases = [Path('C:/browser-test/user'), Path('C:/browser-test/program64'), Path('C:/browser-test/program32')]
        environ = dict(zip(('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'), map(str, bases)))
        candidates = [base / WINDOWS_EXECUTABLES['Firefox'] for base in bases]
        with self.operating_system('nt', 'Windows', environ):
            for index, expected in enumerate(candidates):
                self.is_file.reset_mock()
                self.is_file.side_effect = lambda path: path == expected
                with self.subTest(index=index):
                    self.assertEqual(expected, browser_setup.resolve_browser('Firefox'))
                    self.assertEqual(candidates[:index + 1], [call.args[0] for call in self.is_file.call_args_list])

    def test_missing_family_never_opens_another_installed_browser(self):
        base = Path('C:/browser-test/local')
        self.is_file.side_effect = lambda path: path == base / WINDOWS_EXECUTABLES['Chrome']
        with self.operating_system('nt', 'Windows', {'LOCALAPPDATA': str(base)}):
            with self.assertRaisesRegex(ValueError, '^browser_not_found$'):
                browser_setup.open_extensions_page('Firefox')
        self.process.Popen.assert_not_called()

    def test_mac_launches_exact_app_and_fixed_page_without_shell(self):
        with self.operating_system('posix', 'Darwin'):
            for family, app in MAC_APPS.items():
                target = Path('/Applications') / app
                self.is_dir.side_effect = lambda path: path == target
                with self.subTest(family=family):
                    self.assertEqual(target, browser_setup.resolve_browser(family))
                    browser_setup.open_extensions_page(family)
                    self.assert_launch(['/usr/bin/open', '-a', str(target), PAGES[family]], 0)
            user_app = self.home / 'Applications' / 'Firefox.app'
            self.is_dir.side_effect = lambda path: path == user_app
            self.assertEqual(user_app, browser_setup.resolve_browser('Firefox'))

    def test_linux_resolves_only_family_specific_commands_and_opens_directly(self):
        commands = {'Chrome': 'google-chrome', 'Edge': 'microsoft-edge', 'Yandex': 'yandex-browser',
                    'Opera': 'opera', 'Brave': 'brave-browser', 'Vivaldi': 'vivaldi',
                    'Chromium': 'chromium', 'Firefox': 'firefox'}
        with self.operating_system('posix', 'Linux'):
            for family, command in commands.items():
                target = Path('/usr/bin') / command
                self.which.reset_mock()
                self.which.side_effect = lambda name: str(target) if name == command else None
                self.is_file.side_effect = lambda path: path == target
                with self.subTest(family=family):
                    browser_setup.open_extensions_page(family)
                    self.which.assert_called_once_with(command)
                    self.assert_launch([str(target), PAGES[family]], 0)

    def test_linux_fallback_and_non_file_results_cannot_launch_default(self):
        with self.operating_system('posix', 'Linux'):
            target = Path('/usr/bin/google-chrome-stable')
            self.which.side_effect = lambda name: str(target) if name == 'google-chrome-stable' else None
            self.is_file.side_effect = lambda path: path == target
            self.assertEqual(target, browser_setup.resolve_browser('Chrome'))
            self.is_file.side_effect = None
            self.is_file.return_value = False
            with self.assertRaisesRegex(ValueError, '^browser_not_found$'):
                browser_setup.open_extensions_page('Chrome')
        self.process.Popen.assert_not_called()

    def test_spawn_failure_exposes_only_safe_error_code(self):
        with self.operating_system('nt', 'Windows'):
            with patch.object(browser_setup, 'resolve_browser', return_value=self.install / 'chrome.exe'):
                self.process.Popen.side_effect = OSError('private installation path and launch details')
                with self.assertRaisesRegex(ValueError, '^browser_open_failed$') as raised:
                    browser_setup.open_extensions_page('Chrome')
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn('private', str(raised.exception))

    def test_discovery_is_metadata_only_and_does_not_claim_firefox_is_signed(self):
        with patch.object(browser_setup, 'resolve_browser', side_effect=lambda name: self.install if name == 'Firefox' else None):
            rows = browser_setup.discover_browsers()
        self.assertEqual(list(PAGES), [row['family'] for row in rows])
        for row in rows:
            self.assertEqual(PAGES[row['family']], row['extension_page'])
            self.assertEqual(row['family'] == 'Firefox', row['installed'])
        firefox = rows[-1]
        self.assertEqual('gecko', firefox['engine'])
        self.assertTrue(firefox['signed_package_required'])
        self.assertEqual('signed_package_required', firefox['support_level'])
        self.process.Popen.assert_not_called()
        self.write.assert_not_called()

    def test_windows_native_host_registry_locations_are_per_family(self):
        prefixes = {'Chrome': r'Software\Google\Chrome', 'Edge': r'Software\Microsoft\Edge',
                    'Yandex': r'Software\Yandex\YandexBrowser', 'Opera': r'Software\Google\Chrome',
                    'Brave': r'Software\BraveSoftware\Brave-Browser', 'Vivaldi': r'Software\Vivaldi',
                    'Chromium': r'Software\Chromium', 'Firefox': r'Software\Mozilla'}
        with self.operating_system('nt', 'Windows'):
            self.assertEqual({key: value + '\\NativeMessagingHosts\\' + native_host.HOST_NAME
                              for key, value in prefixes.items()}, browser_setup.host_locations(self.root))

    def test_mac_native_hosts_have_gecko_location_and_documented_shared_paths(self):
        base = self.home / 'Library' / 'Application Support'
        folders = {'Chrome': base / 'Google' / 'Chrome', 'Edge': base / 'Microsoft Edge',
                   'Yandex': base / 'Yandex' / 'YandexBrowser', 'Opera': base / 'Google' / 'Chrome',
                   'Brave': base / 'Google' / 'Chrome', 'Vivaldi': base / 'Vivaldi', 'Chromium': base / 'Chromium',
                   'Firefox': base / 'Mozilla'}
        with self.operating_system('posix', 'Darwin'):
            self.assertEqual({key: value / 'NativeMessagingHosts' / (native_host.HOST_NAME + '.json')
                              for key, value in folders.items()}, browser_setup.host_locations(self.root))

    def test_linux_native_hosts_honor_xdg_and_do_not_write_system_opera_path(self):
        config = Path('/browser-test/xdg')
        folders = {'Chrome': config / 'google-chrome', 'Edge': config / 'microsoft-edge',
                   'Yandex': config / 'yandex-browser', 'Brave': config / 'BraveSoftware' / 'Brave-Browser',
                   'Vivaldi': config / 'vivaldi', 'Chromium': config / 'chromium'}
        expected = {key: value / 'NativeMessagingHosts' / (native_host.HOST_NAME + '.json') for key, value in folders.items()}
        expected['Firefox'] = self.home / '.mozilla' / 'native-messaging-hosts' / (native_host.HOST_NAME + '.json')
        with self.operating_system('posix', 'Linux', {'XDG_CONFIG_HOME': str(config)}):
            self.assertEqual(expected, browser_setup.host_locations(self.root))
        with self.operating_system('posix', 'Linux'):
            locations = browser_setup.host_locations(self.root)
            self.assertEqual(self.home / '.config' / 'google-chrome' / 'NativeMessagingHosts' /
                             (native_host.HOST_NAME + '.json'), locations['Chrome'])
            self.assertNotIn('Opera', locations)

    def test_host_manifests_never_mix_gecko_ids_with_chromium_origins(self):
        executable = self.install / 'SoftTrackingHost.exe'
        chromium = native_host.host_manifest(executable, 'chromium')
        gecko = native_host.host_manifest(executable, 'gecko')
        for manifest in (chromium, gecko):
            self.assertEqual(str(executable), manifest['path'])
            self.assertEqual('stdio', manifest['type'])
            self.assertEqual(native_host.HOST_NAME, manifest['name'])
        self.assertEqual([native_host.ALLOWED_ORIGIN], chromium['allowed_origins'])
        self.assertNotIn('allowed_extensions', chromium)
        self.assertEqual([native_host.FIREFOX_EXTENSION_ID], gecko['allowed_extensions'])
        self.assertNotIn('allowed_origins', gecko)
        with self.assertRaises(ValueError):
            native_host.host_manifest(executable, 'unknown')

    def test_source_interpreter_cannot_register_browser_hosts(self):
        with patch.object(sys, 'frozen', False, create=True):
            with self.assertRaises(RuntimeError):
                browser_setup.register_host(self.install / 'python.exe', self.root)
        self.mkdir.assert_not_called()
        self.write.assert_not_called()

    def test_windows_registration_writes_own_manifests_and_both_hkcu_views(self):
        registry = self.registry()
        executable = self.install / 'SoftTrackingHost.exe'
        with self.operating_system('nt', 'Windows'):
            locations = browser_setup.host_locations(self.root)
            with patch.object(sys, 'frozen', True, create=True), patch.dict(sys.modules, {'winreg': registry}):
                self.assertEqual(list(locations), browser_setup.register_host(executable, self.root))
        written = {call.args[0]: json.loads(call.args[1]) for call in self.write.call_args_list}
        chrome_path = self.root / (native_host.HOST_NAME + '.json')
        firefox_path = self.root / (native_host.HOST_NAME + '.firefox.json')
        self.assertEqual({chrome_path, firefox_path}, set(written))
        self.assertEqual([native_host.ALLOWED_ORIGIN], written[chrome_path]['allowed_origins'])
        self.assertEqual([native_host.FIREFOX_EXTENSION_ID], written[firefox_path]['allowed_extensions'])
        self.assertEqual(len(locations) * 2, registry.CreateKeyEx.call_count)
        for family, path in locations.items():
            for view in (registry.KEY_WOW64_32KEY, registry.KEY_WOW64_64KEY):
                access = registry.KEY_WRITE | view
                registry.CreateKeyEx.assert_any_call(registry.HKEY_CURRENT_USER, path, 0, access)
                target = firefox_path if family == 'Firefox' else chrome_path
                registry.SetValueEx.assert_any_call((registry.HKEY_CURRENT_USER, path, 0, access), '', 0,
                                                   registry.REG_SZ, str(target))
        self.process.Popen.assert_not_called()

    def test_posix_registration_writes_only_expected_user_native_manifests(self):
        for system in ('Darwin', 'Linux'):
            self.write.reset_mock()
            with self.subTest(system=system), self.operating_system('posix', system):
                locations = browser_setup.host_locations(self.root)
                with patch.object(sys, 'frozen', True, create=True):
                    browser_setup.register_host(self.install / 'soft-tracking-host', self.root)
            written = {call.args[0]: json.loads(call.args[1]) for call in self.write.call_args_list}
            self.assertEqual(set(locations.values()), set(written))
            for family, path in locations.items():
                key = 'allowed_extensions' if family == 'Firefox' else 'allowed_origins'
                self.assertIn(key, written[path])
                self.assertEqual('stdio', written[path]['type'])
        self.process.Popen.assert_not_called()

    def test_registration_health_rejects_wrong_engine_scope_path_or_missing_host(self):
        executable = self.install / 'soft-tracking-host'
        paths = {'Chrome': self.root / 'chrome.json', 'Firefox': self.root / 'firefox.json'}
        valid = {'Chrome': native_host.host_manifest(executable),
                 'Firefox': native_host.host_manifest(executable, 'gecko')}
        self.is_file.return_value = True
        with self.operating_system('posix', 'Linux'), patch.object(browser_setup, 'host_locations', return_value=paths):
            manifests = {paths[key]: value for key, value in valid.items()}
            with patch.object(browser_health, 'read_json', side_effect=lambda path, default: manifests.get(path, default)):
                self.assertEqual([('Chrome', True), ('Firefox', True)], browser_health.registration(self.install, self.root))
                for invalid in (valid['Chrome'], dict(valid['Firefox'], allowed_origins=[native_host.ALLOWED_ORIGIN]),
                                dict(valid['Firefox'], allowed_extensions=['wrong@example.test']),
                                dict(valid['Firefox'], path=str(self.install / 'wrong-host'))):
                    manifests[paths['Firefox']] = invalid
                    self.assertEqual([('Chrome', True), ('Firefox', False)], browser_health.registration(self.install, self.root))
                manifests[paths['Firefox']] = valid['Firefox']
                self.is_file.return_value = False
                self.assertEqual([('Chrome', False), ('Firefox', False)], browser_health.registration(self.install, self.root))
        self.write.assert_not_called()

    def test_windows_registration_health_reads_hkcu_without_modifying_registry(self):
        registry = self.registry()
        manifest_path = self.root / 'firefox.json'
        registry.QueryValueEx.return_value = (str(manifest_path), registry.REG_SZ)
        self.is_file.return_value = True
        location = r'Software\Mozilla\NativeMessagingHosts\com.soft.tracking'
        with self.operating_system('nt', 'Windows'), patch.dict(sys.modules, {'winreg': registry}):
            with patch.object(browser_setup, 'host_locations', return_value={'Firefox': location}):
                manifest = native_host.host_manifest(self.install / 'SoftTrackingHost.exe', 'gecko')
                with patch.object(browser_health, 'read_json', return_value=manifest):
                    self.assertEqual([('Firefox', True)], browser_health.registration(self.install, self.root))
                    registry.OpenKey.assert_called_once_with(registry.HKEY_CURRENT_USER, location)
                    registry.OpenKey.side_effect = OSError('not registered')
                    self.assertEqual([('Firefox', False)], browser_health.registration(self.install, self.root))
        registry.CreateKeyEx.assert_not_called()
        registry.SetValueEx.assert_not_called()
        self.write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
