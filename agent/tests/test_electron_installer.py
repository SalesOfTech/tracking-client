import base64
import hashlib
import io
import json
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from nacl.signing import SigningKey

from agent_tracker import installer
from agent_tracker.core.client import Client
from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.core.release_manager import executable_name
from agent_tracker.core.target import runtime_target
from agent_tracker.electron_desktop import Controller, serve


class ElectronInstallerTests(unittest.TestCase):
    company_code = 'a' * 32
    employee_key = 'c' * 64

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tracking-controller-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bundle = self.root / 'bundle'
        self.bundle.mkdir()
        self.workspace = self.root / 'workspace'
        self.install_root = self.workspace / 'install'
        self.controller = Controller(bundle=self.bundle)
        self.controller.language = 'cs'
        self.patch('agent_tracker.electron_desktop.workspace', return_value=self.workspace)
        self.patch('agent_tracker.automatic_migration.workspace', return_value=self.workspace)
        self.patch('agent_tracker.automatic_migration.legacy_discovery.discover', return_value=None)
        environment = patch.dict('os.environ', SOFT_TRACKING_INSTALL=str(self.install_root))
        environment.start()
        self.addCleanup(environment.stop)
        self.integration = {name: self.patch('agent_tracker.installer.' + name)
                            for name in ('protect_workspace', 'register_host', 'autostart',
                                         'shortcuts', 'register_uninstaller')}
        self.retire = self.patch('agent_tracker.legacy_migration.replace_current_user')
        self.http = self.patch('agent_tracker.core.networking.HttpClient.post_json',
                               side_effect=AssertionError('Unexpected network request'))
        self.patch('requests.sessions.Session.request',
                   side_effect=AssertionError('Live network is forbidden in installer tests'))
        self.install = self.patch('agent_tracker.installer.install', wraps=installer.install)
        self.spawn = self.patch('agent_tracker.electron_desktop.subprocess.Popen')

    def patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def make_bundle(self):
        system, architecture = runtime_target()
        build = dict(version='3.0.1', os=system, architecture=architecture,
                     target=system + '-' + architecture + '-modern', launcher_protocol=1)
        archive = self.bundle / 'release.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('build.json', json.dumps(build))
            package.writestr('extension/manifest.json', json.dumps({'version': build['version']}))
            for native in (False, True):
                for folder, bootstrap in (('app', False), ('launcher', True)):
                    entry = zipfile.ZipInfo(folder + '/' + executable_name(native, bootstrap))
                    entry.external_attr = (stat.S_IFREG | 0o755) << 16
                    package.writestr(entry, b'executable fixture')
        raw = archive.read_bytes()
        manifest = dict(build, protocol=3, expires_at=int(time.time()) + 3600,
                        sha256=hashlib.sha256(raw).hexdigest(), size=len(raw),
                        url='https://example.test/release.zip')
        key = SigningKey.generate()
        payload = json.dumps(manifest).encode()
        atomic_json(self.bundle / 'setup-build.json', build)
        atomic_json(self.bundle / 'trusted-update-keys.json',
                    {'test': base64.b64encode(bytes(key.verify_key)).decode()})
        atomic_json(self.bundle / 'manifest.json', {
            'key_id': 'test', 'payload': base64.b64encode(payload).decode(),
            'signature': base64.b64encode(key.sign(payload).signature).decode()})

    def finish_job(self):
        self.assertIsNotNone(self.controller.job)
        self.controller.job.join(timeout=5)
        self.assertFalse(self.controller.job.is_alive())
        self.assertFalse(self.controller.busy)
        return self.controller.command('status', {})

    def assert_installed_and_launch(self):
        view = self.finish_job()
        self.assertEqual(view['phase'], 'complete')
        self.assertEqual(view['error'], '')
        self.assertEqual(view['code'], self.company_code)
        self.install.assert_called_once_with(self.bundle, self.install_root, self.company_code,
                                             language='cs', retire_legacy=False)
        self.assertEqual(read_json(self.install_root / 'current.json'), {'version': '3.0.1'})
        self.assertEqual(read_json(self.install_root / 'enrollment.json'), {
            'company_code': self.company_code, 'language': 'cs', 'language_source': 'manual'})
        launcher = self.install_root / executable_name(False, True)
        self.assertEqual(launcher.read_bytes(), b'executable fixture')
        self.assertTrue((self.install_root / 'versions/3.0.1/app' / executable_name()).is_file())
        self.assertTrue((self.install_root / 'extension/manifest.json').is_file())
        self.integration['protect_workspace'].assert_called_once_with(self.workspace)
        self.integration['register_host'].assert_called_once_with(
            self.install_root / executable_name(True, True), self.workspace)
        self.integration['autostart'].assert_called_once_with(launcher, True)
        self.integration['shortcuts'].assert_called_once_with(self.install_root)
        self.integration['register_uninstaller'].assert_called_once_with(self.install_root)
        self.spawn.assert_not_called()
        self.assertFalse(self.controller.exit_requested)
        self.controller.command('launch', {})
        self.spawn.assert_called_once_with([str(launcher)])
        self.assertTrue(self.controller.exit_requested)

    def test_entered_company_code_installs_persistently_then_launches_installed_app(self):
        self.make_bundle()
        self.controller.command('install', {'code': self.company_code})
        self.assert_installed_and_launch()
        self.http.assert_not_called()
        self.retire.assert_not_called()

    def test_entered_employee_key_resolves_before_install_and_persists_enrollment(self):
        self.make_bundle()
        calls = []

        def respond(path, payload):
            calls.append(path)
            self.assertEqual(payload['employee_key'], self.employee_key)
            if path == '/client/v3/installation':
                self.assertFalse(self.install_root.exists())
                self.install.assert_not_called()
                return dict(ok=True, company_code=self.company_code, company_id=32, user_id=100)
            self.assertEqual(path, '/client/v3/enroll')
            self.assertTrue((self.install_root / 'current.json').is_file())
            self.assertEqual(payload['company_code'], self.company_code)
            return dict(ok=True, device_id=payload['device_id'], company_id=32, user_id=100,
                        company_name='Fixture company', user_name='Fixture employee')

        self.http.side_effect = respond
        data = {'code': self.employee_key}
        self.controller.command('install', data)
        self.assert_installed_and_launch()
        self.assertEqual(calls, ['/client/v3/installation', '/client/v3/enroll'])
        self.assertEqual(data['code'], '')
        self.retire.assert_called_once_with(self.install_root)
        client = Client(self.workspace)
        try:
            self.assertEqual(client.state.get('identity')['user_id'], 100)
            self.assertEqual(client.state.get('identity')['company_id'], 32)
        finally:
            client.close()
            client.http.session.close()

    def test_closing_clean_setup_before_entering_key_does_not_install(self):
        executable = self.bundle / 'setup-ui'
        executable.write_bytes(b'UI fixture')
        child = Mock(returncode=0)
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO(
            b'{"id":1,"action":"ready","input":{}}\n{"event":"closing"}\n')
        child.poll.side_effect = lambda: 0 if child.stdin.closed else None
        self.spawn.return_value = child
        with patch('agent_tracker.electron_desktop.restore_links'), \
                patch('agent_tracker.electron_desktop.electron_path', return_value=executable):
            self.assertEqual(serve(self.controller, self.bundle / 'electron'), 0)
        self.assertEqual(self.finish_job()['phase'], 'waiting')
        self.assertIsNone(self.controller.launcher)
        self.assertFalse(self.workspace.exists())
        self.install.assert_not_called()
        self.http.assert_not_called()
        self.retire.assert_not_called()
        for integration in self.integration.values():
            integration.assert_not_called()
        self.spawn.assert_called_once()  # Only the setup UI, never the installed launcher.
        self.assertEqual(self.spawn.call_args.args[0], [str(executable)])

    def test_missing_or_invalid_key_cannot_install_or_launch(self):
        for data in ({}, {'code': ''}, {'code': 'short'}, {'code': 'g' * 64}):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'setup_code_required'):
                self.controller.command('install', data)
        with self.assertRaisesRegex(ValueError, 'invalid_request'):
            self.controller.command('launch', {})
        self.assertIsNone(self.controller.job)
        self.assertFalse(self.workspace.exists())
        self.install.assert_not_called()
        self.spawn.assert_not_called()

    def test_rejected_employee_key_does_not_reach_persistent_installer(self):
        self.http.side_effect = None
        self.http.return_value = {'ok': False}
        self.controller.command('install', {'code': self.employee_key})
        view = self.finish_job()
        self.assertEqual(view['phase'], 'failed')
        self.assertEqual(view['error'], 'setup_company_unavailable')
        self.install.assert_not_called()
        self.assertFalse(self.workspace.exists())
        with self.assertRaisesRegex(ValueError, 'invalid_request'):
            self.controller.command('launch', {})
        self.spawn.assert_not_called()

    def test_damaged_package_cannot_report_success_or_launch(self):
        self.make_bundle()
        (self.bundle / 'release.zip').write_bytes(b'damaged archive')
        self.controller.command('install', {'code': self.company_code})
        view = self.finish_job()
        self.assertEqual(view['phase'], 'failed')
        self.assertEqual(view['error'], 'setup_failed')
        self.assertEqual(self.install.call_count, 1)
        self.assertFalse((self.install_root / 'current.json').exists())
        with self.assertRaisesRegex(ValueError, 'invalid_request'):
            self.controller.command('launch', {})
        self.spawn.assert_not_called()
        for integration in self.integration.values():
            integration.assert_not_called()


if __name__ == '__main__':
    unittest.main()
