"""Harness contracts and real local TLS, not a claim of frozen native acceptance."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests
from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import smoke_auto_update as smoke

ClientState, EventQueue, atomic_json, signed = smoke.client_modules()
from agent_tracker.core.employee_profiles import EmployeeProfiles
from agent_tracker.core.target import runtime_target


class HarnessTests(unittest.TestCase):
    baseline_version = '3.5.0'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='auto-update-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.old, self.new = self.root / 'old', self.root / 'new'
        self.key = SigningKey.generate()
        self.keys = {'fixture': base64.b64encode(bytes(self.key.verify_key)).decode()}
        self.old.mkdir()
        self.new.mkdir()
        self.target = '-'.join((*runtime_target(), 'modern'))
        self.baseline = self.bundle(self.old, self.baseline_version)
        self.candidate = self.bundle(self.new, '3.5.1')

    def envelope(self, manifest):
        raw = json.dumps(manifest).encode()
        return {'key_id': 'fixture', 'payload': base64.b64encode(raw).decode(),
                'signature': base64.b64encode(self.key.sign(raw).signature).decode()}

    def bundle(self, path, version):
        setup = path / 'setup-payload'
        setup.mkdir()
        build = dict(version=version, target=self.target, os=runtime_target()[0],
                     architecture=runtime_target()[1], launcher_protocol=1)
        archive = path / ('release-' + version + '.zip')
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('build.json', json.dumps(build))
            package.writestr('extension/manifest.json', json.dumps({'version': version, 'key': 'fixture'}))
            for native in (False, True):
                package.writestr('launcher/' + smoke.executable(native, True), b'fictional launcher')
                package.writestr('app/' + smoke.executable(native), b'fictional runtime')
        manifest = dict(build, protocol=3, expires_at=int(time.time()) + 3600,
                        url='https://' + smoke.HOSTS[0] + '/client/v3/releases/' + self.target + '/' + version + '.zip',
                        size=archive.stat().st_size, sha256=smoke.digest(archive))
        atomic_json(path / 'manifest.json', self.envelope(manifest))
        atomic_json(setup / 'manifest.json', self.envelope(manifest))
        atomic_json(setup / 'setup-build.json', build)
        atomic_json(setup / 'trusted-update-keys.json', self.keys)
        (setup / 'release.zip').write_bytes(archive.read_bytes())
        return manifest

    def prepare(self):
        return smoke.prepare_install(self.old, self.new, self.root / 'install', signed, atomic_json)

    def test_only_old_frozen_payload_is_installed_and_failure_is_legacy(self):
        baseline, candidate, envelope, archive = self.prepare()
        root = self.root / 'install'
        self.assertEqual(baseline['version'], self.baseline_version)
        self.assertEqual(candidate, self.candidate)
        self.assertEqual(envelope, (self.new / 'manifest.json').read_bytes())
        self.assertEqual(smoke.digest(archive), candidate['sha256'])
        self.assertFalse((root / 'versions/3.5.1').exists())
        self.assertEqual(smoke.read_json(root / 'current.json'), {'version': self.baseline_version})
        self.assertEqual(set(smoke.read_json(root / 'failed.json')), {'version', 'time'})
        self.assertEqual(smoke.read_json(root / 'failed.json')['version'], self.baseline_version)
        self.assertFalse((root / 'update-request.json').exists())

    def test_rejects_other_baseline_even_with_valid_signature(self):
        self.assertEqual(smoke.SUPPORTED_BASELINES, {'3.4.2', '3.5.0'})
        value = dict(self.baseline, version='3.4.0')
        atomic_json(self.old / 'setup-payload/setup-build.json', value)
        atomic_json(self.old / 'setup-payload/manifest.json', self.envelope(value))
        with self.assertRaisesRegex(RuntimeError, 'Unsupported automatic-update baseline'):
            self.prepare()
        self.assertFalse((self.root / 'install').exists())

    def test_rejects_mismatched_supported_setup_and_signed_versions(self):
        other = next(version for version in smoke.SUPPORTED_BASELINES if version != self.baseline_version)
        value = dict(self.baseline, version=other)
        atomic_json(self.old / 'setup-payload/setup-build.json', value)
        with self.assertRaisesRegex(RuntimeError, 'Signed baseline does not match setup metadata'):
            self.prepare()
        self.assertFalse((self.root / 'install').exists())

    def test_rejects_invalid_setup_metadata_before_installation(self):
        for build in (None, [], {}, {'version': ['3.4.2']}, {'version': 342}, {'version': '../3.4.2'}):
            atomic_json(self.old / 'setup-payload/setup-build.json', build)
            with self.subTest(build=build), self.assertRaisesRegex(RuntimeError, 'Unsupported automatic-update baseline'):
                self.prepare()
            self.assertFalse((self.root / 'install').exists())

    def test_rejects_signed_archive_with_mismatched_embedded_build_version(self):
        archive = self.old / 'setup-payload/release.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            package.writestr('build.json', json.dumps(dict(self.baseline, version='3.4.0')))
        manifest = dict(self.baseline, size=archive.stat().st_size, sha256=smoke.digest(archive))
        atomic_json(self.old / 'setup-payload/manifest.json', self.envelope(manifest))
        with self.assertRaisesRegex(RuntimeError, 'Invalid baseline installation layout'):
            self.prepare()
        self.assertFalse((self.root / 'install/current.json').exists())

    def test_candidate_cannot_install_its_own_trust_key(self):
        self.key = SigningKey.generate()
        atomic_json(self.new / 'manifest.json', self.envelope(self.candidate))
        atomic_json(self.new / 'setup-payload/trusted-update-keys.json',
                    {'fixture': base64.b64encode(bytes(self.key.verify_key)).decode()})
        with self.assertRaises(Exception):
            self.prepare()
        self.assertFalse((self.root / 'install').exists())

    def test_candidate_downgrade_wrong_target_external_origin_and_bad_hash_fail(self):
        for change in ({'version': self.baseline_version}, {'version': '3.4.0'}, {'target': 'linux-arm64-legacy'},
                       {'url': 'https://example.test/release.zip'}, {'sha256': '0' * 64}):
            atomic_json(self.new / 'manifest.json', self.envelope(dict(self.candidate, **change)))
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError)):
                self.prepare()
            self.assertFalse((self.root / 'install').exists())

    def test_data_checks_detect_false_ack(self):
        device, identity, events = smoke.seed_data(self.root, ClientState, EventQueue)
        state = ClientState(self.root)
        try:
            EmployeeProfiles(state)  # The old runtime performs this normal migration.
        finally:
            state.close()
        smoke.assert_data(self.root, device, identity, events, ClientState, EventQueue)
        queue = EventQueue(self.root / 'outbox.sqlite3')
        try:
            queue.acknowledge([events[0]['event_id']])
        finally:
            queue.close()
        with self.assertRaisesRegex(RuntimeError, 'Pending event'):
            smoke.assert_data(self.root, device, identity, events, ClientState, EventQueue)

    def test_data_checks_detect_identity_change(self):
        device, identity, events = smoke.seed_data(self.root, ClientState, EventQueue)
        state = ClientState(self.root)
        try:
            EmployeeProfiles(state)
            state.set('identity', dict(identity, user_id=999))
        finally:
            state.close()
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            smoke.assert_data(self.root, device, identity, events, ClientState, EventQueue)

    def test_data_checks_detect_rewritten_quarantined_payload(self):
        device, identity, events = smoke.seed_data(self.root, ClientState, EventQueue)
        state = ClientState(self.root)
        try:
            EmployeeProfiles(state)
        finally:
            state.close()
        import sqlite3
        with sqlite3.connect(self.root / 'outbox.sqlite3') as db:
            db.execute('UPDATE outbox SET payload=? WHERE event_id=?',
                       (json.dumps(dict(events[1], url='https://wrong.test/')), events[1]['event_id']))
        db.close()
        with self.assertRaisesRegex(RuntimeError, 'Rejected payload changed'):
            smoke.assert_data(self.root, device, identity, events, ClientState, EventQueue)

    def test_child_env_drops_credentials_and_inherited_test_or_proxy_controls(self):
        before = dict(os.environ)
        with patch.dict(os.environ, {'GH_TOKEN': 'not-a-real-token', 'HTTPS_PROXY': 'http://external.test',
                                     'NO_PROXY': '*', 'SOFT_TRACKING_RUN_TOKEN': 'stale',
                                     'PYTHONPATH': '/untrusted', 'REQUESTS_CA_BUNDLE': '/untrusted'}):
            env = smoke.child_environment(self.root, self.root / 'install', 'http://127.0.0.1:1234', self.root / 'tls.pem')
        self.assertEqual(os.environ, before)
        for name in ('GH_TOKEN', 'SOFT_TRACKING_RUN_TOKEN', 'PYTHONPATH'):
            self.assertNotIn(name, env)
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:1234')
        self.assertNotIn('*', env['NO_PROXY'])
        self.assertEqual(env['REQUESTS_CA_BUNDLE'], str(self.root / 'tls.pem'))

    def test_runtime_readiness_requires_new_supervisor_and_persistent_runtime(self):
        root = self.root / 'install'
        marker = root / 'run' / ('a' * 32 + '.ready')
        atomic_json(marker, {'ready': True})
        path = root / 'versions/3.5.1/app' / smoke.executable()
        runtime = Mock()
        runtime.exe.return_value = str(path)
        runtime.cmdline.return_value = [str(path), '--autostart']
        runtime.environ.return_value = {'SOFT_TRACKING_HEALTH': str(marker), 'SOFT_TRACKING_RUN_TOKEN': marker.stem}
        supervisor = Mock()
        supervisor.exe.return_value = str(path)
        supervisor.cmdline.return_value = [str(path), '--supervisor']
        self.assertIsNone(smoke.runtime_ready([runtime], root, '3.5.1'))
        self.assertEqual(smoke.runtime_ready([supervisor, runtime], root, '3.5.1'), marker)
        runtime.cmdline.return_value = [str(path), '--health-check']
        self.assertIsNone(smoke.runtime_ready([supervisor, runtime], root, '3.5.1'))
        self.assertIsNone(smoke.runtime_ready([supervisor, runtime], root, self.baseline_version))

    def fixture(self):
        cert, tls = smoke.make_certificate(self.root)
        device = {'device_id': 'a' * 32, 'device_secret': 'b' * 64}
        fixture = smoke.Fixture(self.candidate, (self.new / 'manifest.json').read_bytes(),
                                self.new / 'release-3.5.1.zip', device)
        fixture.release.set()
        return cert, tls, fixture

    def session(self, proxy, cert):
        session = requests.Session()
        self.addCleanup(session.close)
        session.trust_env = False
        session.proxies = {'https': proxy, 'http': proxy}
        session.verify = str(cert)
        session.headers['Authorization'] = 'Bearer ' + 'b' * 64
        return session

    def test_real_https_serves_signed_bytes_and_archive_only_with_auth(self):
        cert, tls, fixture = self.fixture()
        with smoke.local_server(fixture, tls) as proxy:
            session = self.session(proxy, cert)
            url = 'https://' + smoke.HOSTS[0] + fixture.catalog
            response = session.get(url, headers={'X-Tracking-Client-Version': self.baseline_version}, timeout=5)
            self.assertEqual(response.content, fixture.envelope)
            manifest = signed.verify_manifest(response.json(), self.keys, self.baseline_version, *runtime_target())
            response = session.get(manifest['url'], timeout=5)
            self.assertEqual(hashlib.sha256(response.content).hexdigest(), manifest['sha256'])
            self.assertTrue(fixture.seen('catalog', self.baseline_version))
            self.assertTrue(fixture.seen('download_complete'))
            session.headers.pop('Authorization')
            self.assertEqual(session.get(url, timeout=5).status_code, 401)
        self.assertFalse(any(thread.name == 'auto-update-fixture' for thread in threading.enumerate()))

    def test_subprocess_uses_requests_environment_without_disabling_validation(self):
        cert, tls, fixture = self.fixture()
        with smoke.local_server(fixture, tls) as proxy:
            env = smoke.child_environment(self.root, self.root / 'install', proxy, cert)
            code = ('import requests, sys; s=requests.Session(); '
                    's.headers["Authorization"]="Bearer "+"b"*64; '
                    'r=s.get(sys.argv[1], timeout=5); r.raise_for_status(); '
                    'assert s.verify is True and s.trust_env is True; '
                    'assert r.content == open(sys.argv[2], "rb").read()')
            result = subprocess.run([sys.executable, '-B', '-c', code,
                                     'https://' + smoke.HOSTS[1] + fixture.catalog,
                                     str(self.new / 'manifest.json')], env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_cleanup_stops_owned_subprocess_on_failure(self):
        process = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(60)'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tree = smoke.ProcessTree(process)
        try:
            with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                try:
                    raise RuntimeError('fixture failure')
                finally:
                    tree.cleanup()
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    def test_untrusted_tls_fails_and_proxy_never_forwards_unknown_hosts(self):
        cert, tls, fixture = self.fixture()
        with smoke.local_server(fixture, tls) as proxy:
            session = self.session(proxy, cert)
            with self.assertRaises(requests.exceptions.SSLError):
                session.get('https://' + smoke.HOSTS[0] + fixture.catalog, verify=True, timeout=5)
            with self.assertRaises(requests.exceptions.ProxyError):
                session.get('https://example.test/release.zip', timeout=5)
            self.assertTrue(fixture.seen('blocked_connect'))
            self.assertEqual(session.get('http://' + smoke.HOSTS[0] + fixture.catalog, timeout=5).status_code, 403)

    def test_config_disables_collection_and_events_never_acknowledge_pending(self):
        cert, tls, fixture = self.fixture()
        with smoke.local_server(fixture, tls) as proxy:
            session = self.session(proxy, cert)
            base = 'https://' + smoke.HOSTS[0]
            response = session.post(base + '/client/v3/config', json={'version': '3.5.1'}, timeout=5)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['config']['tracking'])
            self.assertTrue(fixture.seen('config', '3.5.1'))
            response = session.post(base + '/client/v3/events', json={'events': []}, timeout=5)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('accepted', response.json())

    def test_server_is_closed_even_when_harness_raises(self):
        cert, tls, fixture = self.fixture()
        address = None
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with smoke.local_server(fixture, tls) as proxy:
                address = ('127.0.0.1', int(proxy.rsplit(':', 1)[1]))
                raise RuntimeError('fixture failure')
        with self.assertRaises(OSError):
            socket.create_connection(address, timeout=1)

    def test_cli_emits_fixed_failure_stage_without_private_exception_text(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/smoke_auto_update.py'),
                                 str(self.root / 'private-old'), str(self.new)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.decode().strip(), 'FAIL: automatic-update stage=prepare exception=RuntimeError')
        self.assertNotIn(str(self.root).encode(), result.stdout + result.stderr)

    def test_tool_has_no_installer_or_release_manager_update_calls(self):
        import ast
        tree = ast.parse(Path(smoke.__file__).read_text())
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {'activate', 'confirm', 'rollback', 'install', 'upgrade_existing', 'check', 'download'})


class Baseline342HarnessTests(HarnessTests):
    baseline_version = '3.4.2'


if __name__ == '__main__':
    unittest.main()
