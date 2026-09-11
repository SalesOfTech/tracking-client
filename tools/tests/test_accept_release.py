"""Acceptance/promotion contracts use fictional archives and mocked GitHub only."""
import base64
from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

from nacl.signing import SigningKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import accept_release as gate


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class GateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        network = patch.object(gate.requests, 'Session', side_effect=AssertionError('Network forbidden in unit tests'))
        network.start()
        self.addCleanup(network.stop)
        self.tag = 'candidate-3.2.0-123-2'
        self.repo = 'fixture/public-client'
        self.key = SigningKey(bytes(range(32)))  # Public, deterministic test fixture only.
        self.keys = {'fixture': base64.b64encode(bytes(self.key.verify_key)).decode()}
        self.contents, assets = {}, {}
        identifier = 100
        for row in gate.TARGETS:
            name = 'SOFT-Tracking-' + row['target'] + '-3.2.0.zip'
            self.contents[name] = ('fictional archive ' + name).encode()
            self.contents[name + '.sha256'] = (hashlib.sha256(self.contents[name]).hexdigest() + '  ' + name + '\n').encode()
            for filename in (name, name + '.sha256'):
                assets[filename] = {'id': identifier, 'size': len(self.contents[filename]),
                                    'sha256': hashlib.sha256(self.contents[filename]).hexdigest()}
                identifier += 1
        self.plan = {'version': '3.2.0', 'repository': self.repo, 'candidate_tag': self.tag,
                     'candidate_sha': 'a' * 40, 'candidate_run_id': 123, 'candidate_run_attempt': 2,
                     'candidate_release_id': 7, 'assets': assets, 'harness_sha': 'b' * 40,
                     'dispatch_actor': 'maintainer', 'triggering_actor': 'maintainer',
                     'acceptance_run_id': 456, 'acceptance_run_attempt': 1, 'trusted_keys': self.keys}
        environment = patch.dict(os.environ, {'RUNNER_TEMP': str(self.root), 'GITHUB_REPOSITORY': self.repo,
                          'GITHUB_SHA': 'b' * 40, 'GITHUB_RUN_ID': '456', 'GITHUB_RUN_ATTEMPT': '1',
                          'ACCEPTANCE_PLAN': json.dumps(self.plan), 'GITHUB_ACTOR': 'maintainer',
                          'GITHUB_TRIGGERING_ACTOR': 'maintainer'})
        environment.start()
        self.addCleanup(environment.stop)

    def release(self, assets=None):
        return {'id': 7, 'tag_name': self.tag, 'draft': False, 'prerelease': True,
                'assets': [dict(name=name, state='uploaded', digest='sha256:' + item['sha256'],
                                id=item['id'], size=item['size']) for name, item in
                           (self.plan['assets'] if assets is None else assets).items()]}

    def run_fixture(self):
        return {'id': 123, 'run_attempt': 2, 'event': 'workflow_dispatch', 'head_branch': 'main',
                'status': 'completed', 'conclusion': 'success', 'head_sha': 'a' * 40, 'workflow_id': 99,
                'head_repository': {'full_name': self.repo}, 'path': '.github/workflows/release.yml'}

    def candidate_api(self):
        api = Mock(repo=self.repo)
        data = {'actions/runs/123': self.run_fixture(), 'actions/workflows/release.yml': {'id': 99},
                'git/ref/tags/' + self.tag: {'ref': 'refs/tags/' + self.tag, 'object': {'type': 'commit', 'sha': 'a' * 40}},
                'releases/tags/' + self.tag: self.release()}
        api.api.side_effect = lambda path: copy.deepcopy(data[path])
        api.jobs.return_value = [{'name': 'build (' + row['target'] + ')', 'conclusion': 'success'} for row in gate.TARGETS] + [
            {'name': 'publish-candidate', 'conclusion': 'success'}]
        return api, data

    def test_candidate_tag_binds_normal_version_run_and_attempt(self):
        self.assertEqual(gate.candidate_identity(self.tag), (123, 2))
        for bad in ('v3.2.0', 'candidate-3.2.0-rc.1-123-2', 'candidate-3.2.0-123-0', self.tag + '\n', 'https://example.test/'):
            with self.subTest(tag=bad), self.assertRaises(ValueError):
                gate.candidate_identity(bad)

    def test_candidate_requires_exact_successful_workflow_attempt_and_tag_sha(self):
        api, data = self.candidate_api()
        self.assertEqual(gate.candidate_snapshot(api, self.tag)['assets'], self.plan['assets'])
        for field, value in (('run_attempt', 3), ('conclusion', 'failure'), ('event', 'pull_request'),
                             ('head_branch', 'feature'), ('workflow_id', 88), ('path', 'other.yml')):
            original = data['actions/runs/123'][field]
            data['actions/runs/123'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.candidate_snapshot(api, self.tag)
            data['actions/runs/123'][field] = original
        data['git/ref/tags/' + self.tag]['object']['sha'] = 'c' * 40
        with self.assertRaises(ValueError):
            gate.candidate_snapshot(api, self.tag)

    def test_candidate_missing_native_target_cannot_pass(self):
        api, _ = self.candidate_api()
        api.jobs.return_value.pop(0)
        with self.assertRaises(ValueError):
            gate.candidate_snapshot(api, self.tag)

    def test_candidate_draft_or_missing_api_digest_is_rejected(self):
        api, data = self.candidate_api()
        data['releases/tags/' + self.tag]['draft'] = True
        with self.assertRaises(ValueError):
            gate.candidate_snapshot(api, self.tag)
        release = self.release()
        release['assets'][0]['digest'] = None
        with self.assertRaises(ValueError):
            gate.candidate_assets(release)

    def test_asset_set_requires_exact_sixteen_distinct_uploaded_assets(self):
        for change in ('missing', 'duplicate', 'extra', 'uploading', 'too_large'):
            release = self.release()
            if change == 'missing':
                release['assets'].pop()
            elif change == 'duplicate':
                release['assets'][0] = release['assets'][1]
            elif change == 'extra':
                release['assets'].append(dict(release['assets'][0], name='private-baseline.zip'))
            elif change == 'uploading':
                release['assets'][0]['state'] = 'new'
            else:
                release['assets'][0]['size'] = 2 * 1024 ** 3
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.candidate_assets(release)

    def test_distinct_harness_sha_is_allowed_but_approval_run_attempt_is_bound(self):
        self.assertNotEqual(gate.read_plan()['harness_sha'], gate.read_plan()['candidate_sha'])
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '2'}), self.assertRaises(ValueError):
            gate.read_plan()
        with patch.dict(os.environ, {'GITHUB_SHA': 'c' * 40}), self.assertRaises(ValueError):
            gate.read_plan()

    def test_main_only_environment_allows_maintainer_and_keeps_existing_reviewers(self):
        api = Mock(repo=self.repo)
        environment = {'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}}
        branches = {'total_count': 1, 'branch_policies': [{'name': 'main', 'type': 'branch'}]}
        run = {'head_sha': 'b' * 40, 'head_branch': 'main', 'event': 'workflow_dispatch',
               'path': '.github/workflows/accept-release.yml', 'run_attempt': 1,
               'head_repository': {'full_name': self.repo}, 'actor': {'login': 'maintainer'},
               'triggering_actor': {'login': 'maintainer'}}
        for extra in ({}, {'protection_rules': [{'type': 'required_reviewers', 'reviewers': [{'type': 'User'}]}]}):
            api.api.side_effect = [{'permission': 'write', 'role_name': 'maintain'}, dict(environment, **extra), branches, run]
            gate.authorize_dispatch(api)
        self.assertTrue(all(len(call.args) == 1 for call in api.api.call_args_list))

    def test_unrestricted_environment_or_nonmaintainer_fails(self):
        api = Mock(repo=self.repo)
        api.api.side_effect = [{'permission': 'admin'}, {'deployment_branch_policy': None}]
        with self.assertRaises(ValueError):
            gate.authorize_dispatch(api)
        api.api.side_effect = [{'permission': 'write', 'role_name': 'write'}]
        with self.assertRaises(ValueError):
            gate.authorize_dispatch(api)
        for rules in ([{'name': '*', 'type': 'branch'}], [{'name': 'main', 'type': 'tag'}],
                      [{'name': 'main', 'type': 'branch'}, {'name': 'feature', 'type': 'branch'}]):
            api.api.side_effect = [{'permission': 'admin'},
                                  {'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}},
                                  {'total_count': len(rules), 'branch_policies': rules}]
            with self.assertRaises(ValueError):
                gate.authorize_dispatch(api)

    def test_public_key_map_rejects_missing_malformed_and_duplicate_keys(self):
        self.assertEqual(gate.trusted_keys(json.dumps(self.keys)), self.keys)
        for raw in ('{}', '[]', '{"x":"bad"}', '{"x":"a","x":"b"}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                gate.trusted_keys(raw)

    def urls(self):
        return {old: {row['target']: {'url': 'https://release-assets.githubusercontent.com/fixture?sig=not-real'}
                      for row in gate.TARGETS} for old in ('3.1.0', '3.0.4')}

    def test_url_secret_requires_exact_sixteen_slots_and_no_digest_override(self):
        self.assertEqual(gate.baseline_urls(json.dumps(self.urls())), self.urls())
        for change in ('missing_version', 'missing_target', 'extra', 'hash_override'):
            urls = self.urls()
            if change == 'missing_version':
                urls.pop('3.0.4')
            elif change == 'missing_target':
                urls['3.0.4'].pop(gate.TARGETS[0]['target'])
            elif change == 'extra':
                urls['0.0.1'] = {}
            else:
                urls['3.0.4'][gate.TARGETS[0]['target']]['sha256'] = 'a' * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.baseline_urls(json.dumps(urls))

    def test_url_host_scheme_credentials_and_redirects_are_fail_closed(self):
        for url in ('http://release-assets.githubusercontent.com/a?x=1', 'https://api.github.com/a?x=1',
                    'https://release-assets.githubusercontent.com.evil.test/a?x=1',
                    'https://user:password@release-assets.githubusercontent.com/a?x=1',
                    'https://release-assets.githubusercontent.com:444/a?x=1',
                    'https://release-assets.githubusercontent.com/a?x=1#fragment',
                    'https://release-assets.githubusercontent.com/a?x=1\n'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                gate.asset_url(url)
        response = Mock(status_code=302)
        with self.assertRaises(ValueError):
            gate.receive(response, self.root / 'download', 'a' * 64)

    def test_delegated_download_never_sends_auth_or_follows_redirect(self):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'fixture']
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session = Mock()
        session.get.return_value = response
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        with patch.object(gate.requests, 'Session', return_value=session):
            gate.download_url(self.urls()['3.1.0'][gate.TARGETS[0]['target']]['url'],
                              self.root / 'download', hashlib.sha256(b'fixture').hexdigest())
        self.assertFalse(session.trust_env)
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        self.assertNotIn('headers', session.get.call_args.kwargs)
        self.assertNotIn('auth', session.get.call_args.kwargs)

    def test_download_hash_size_and_expired_response_fail(self):
        for index, (status, data, expected, size) in enumerate(((403, b'fixture', 'a' * 64, None),
                                            (200, b'fixture', 'a' * 64, None),
                                            (200, b'fixture', hashlib.sha256(b'fixture').hexdigest(), 2))):
            response = Mock(status_code=status)
            response.iter_content.return_value = [data]
            path = self.root / ('download-' + str(index))
            with self.assertRaises(ValueError):
                gate.receive(response, path, expected, size)

    def test_native_check_binds_actual_interpreter_and_candidate_checkout(self):
        row = gate.TARGETS[0]
        with patch.object(gate, 'runtime_target', return_value=('windows', 'x64')), \
                patch.object(gate, 'checkout_sha', return_value='a' * 40):
            gate.check_native(row, self.plan)
        with patch.object(gate, 'runtime_target', return_value=('windows', 'x86')), self.assertRaises(ValueError):
            gate.check_native(row, self.plan)
        with patch.object(gate, 'runtime_target', return_value=('windows', 'x64')), \
                patch.object(gate, 'checkout_sha', return_value='c' * 40), self.assertRaises(ValueError):
            gate.check_native(row, self.plan)

    def test_smoke_subprocess_has_no_secrets_and_all_output_is_private(self):
        root = gate.private_root()
        root.mkdir()
        with patch.dict(os.environ, {'TRACKING_BASELINE_URLS': 'sensitive', 'GH_TOKEN': 'sensitive',
                                    'GITHUB_TOKEN': 'sensitive', 'ACTIONS_RUNTIME_TOKEN': 'sensitive',
                                    'PYTHONPATH': '/wrong/client', 'PYTHONOPTIMIZE': '1',
                                    'RELEASE_TARGET': gate.TARGETS[0]['target']}), \
                patch.object(gate, 'check_native'), patch.object(gate.subprocess, 'run') as run, \
                redirect_stdout(io.StringIO()):
            gate.test()
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            options = call.kwargs
            self.assertTrue(options['check'])
            self.assertEqual(options['stderr'], subprocess.STDOUT)
            self.assertTrue(Path(options['stdout'].name).is_relative_to(root))
            self.assertNotIn('sensitive', options['env'].values())
            self.assertNotIn('PYTHONPATH', options['env'])
            self.assertNotIn('PYTHONOPTIMIZE', options['env'])
            self.assertEqual(options['env']['TRACKING_SMOKE_CLIENT_ROOT'], str(gate.ROOT / 'candidate'))
            self.assertEqual(call.args[0][-1], '--published')

    def test_smoke_failure_stops_second_baseline_and_is_sanitized(self):
        gate.private_root().mkdir()
        output = io.StringIO()
        with patch.dict(os.environ, {'RELEASE_TARGET': gate.TARGETS[0]['target']}), \
                patch.object(gate, 'hosted_context'), patch.object(gate, 'check_native'), \
                patch.object(gate.sys, 'argv', ['accept_release.py', 'test']), \
                patch.object(gate.subprocess, 'run', side_effect=RuntimeError('private-url-and-output')) as run, \
                redirect_stdout(output):
            self.assertEqual(gate.main(), 1)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('private-url-and-output', output.getvalue())
        self.assertIn('upgrade-3.1.0 FAIL', output.getvalue())

    def test_missing_skipped_or_failed_smoke_step_cannot_promote(self):
        api = Mock()
        good = [{'name': 'accept-' + row['target'], 'status': 'completed', 'conclusion': 'success',
                 'steps': [{'name': gate.SMOKE_STEP, 'conclusion': 'success'}]} for row in gate.TARGETS]
        api.jobs.return_value = good
        gate.accepted_jobs(api, self.plan)
        for change in ('missing', 'duplicate', 'skipped', 'empty_step', 'failed_step'):
            rows = copy.deepcopy(good)
            if change == 'missing':
                rows.pop()
            elif change == 'duplicate':
                rows[0] = rows[1]
            elif change == 'skipped':
                rows[0]['conclusion'] = 'skipped'
            elif change == 'empty_step':
                rows[0]['steps'] = []
            else:
                rows[0]['steps'][0]['conclusion'] = 'failure'
            api.jobs.return_value = rows
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.accepted_jobs(api, self.plan)

    def test_smoke_diagnostics_emit_only_fixed_stage_and_keep_failure_closed(self):
        gate.private_root().mkdir()
        cases = (
            ('AssertionError: Original installed launcher did not start the application', 'old-launcher-startup'),
            ('AssertionError: New frozen version failed its health check', 'new-frozen-health'),
            ('AssertionError: private-value', 'subprocess-failure'),
            ('AssertionError: New frozen version failed its health check private-value', 'subprocess-failure'),
        )
        for diagnostic, stage in cases:
            output = io.StringIO()
            def fail(_command, **options):
                options['stdout'].write(('private-value\n::error::private-value\n' + diagnostic + '\n').encode())
                raise subprocess.CalledProcessError(1, ['private-value'])
            with patch.dict(os.environ, {'RELEASE_TARGET': gate.TARGETS[0]['target']}), \
                    patch.object(gate, 'hosted_context'), patch.object(gate, 'check_native'), \
                    patch.object(gate.sys, 'argv', ['accept_release.py', 'test']), \
                    patch.object(gate.subprocess, 'run', side_effect=fail) as run, redirect_stdout(output):
                self.assertEqual(gate.main(), 1)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(output.getvalue(), gate.TARGETS[0]['target'] + ' 3.2.0 upgrade-3.1.0-' + stage + ' FAIL\n')

    def test_smoke_timeout_is_fixed_enum_without_command_or_output(self):
        gate.private_root().mkdir()
        output = io.StringIO()
        with patch.dict(os.environ, {'RELEASE_TARGET': gate.TARGETS[0]['target']}), \
                patch.object(gate, 'hosted_context'), patch.object(gate, 'check_native'), \
                patch.object(gate.sys, 'argv', ['accept_release.py', 'test']), \
                patch.object(gate.subprocess, 'run', side_effect=subprocess.TimeoutExpired('private-value', 1200)) as run, \
                redirect_stdout(output):
            self.assertEqual(gate.main(), 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(output.getvalue(), gate.TARGETS[0]['target'] + ' 3.2.0 upgrade-3.1.0-process-timeout FAIL\n')

    def test_smoke_diagnostics_read_bounded_tail_and_missing_file_is_generic(self):
        path = self.root / 'private.log'
        path.write_bytes(b'AssertionError: New frozen version failed its health check\n' + b'x' * 65537)
        self.assertEqual(gate.smoke_failure_stage(path), 'subprocess-failure')
        self.assertEqual(gate.smoke_failure_stage(self.root / 'absent.log'), 'subprocess-failure')

    def signed_bundle(self, mutate=None):
        row = gate.TARGETS[0]
        metadata = {'version': '3.2.0', 'target': row['target'], 'os': 'windows', 'architecture': 'x64',
                    'ui': 'electron', 'electron_version': '44.3.0', 'launcher_protocol': 1,
                    'minimum_os_version': '10.0.18362'}
        inner = zipped({'build.json': json.dumps(metadata)})
        manifest = dict(metadata, protocol=3, size=len(inner), sha256=hashlib.sha256(inner).hexdigest(),
                        expires_at=int(time.time()) + 600, url='https://tracking.salesoftech.com/fixture.zip')
        raw = json.dumps(manifest).encode()
        envelope = {'key_id': 'fixture', 'payload': base64.b64encode(raw).decode(),
                    'signature': base64.b64encode(self.key.sign(raw).signature).decode()}
        setup = dict(metadata, file='SOFT-Tracking-Setup-3.2.0.exe', sha256=hashlib.sha256(b'fixture installer').hexdigest())
        provenance = {'version': '3.2.0', 'target': row['target'], 'commit': 'a' * 40,
                      'run_url': 'https://github.com/' + self.repo + '/actions/runs/123', 'build': metadata}
        files = {'manifest.json': json.dumps(envelope), 'release-3.2.0.zip': inner,
                 'provenance.json': json.dumps(provenance), 'setup.json': json.dumps(setup),
                 'SOFT-Tracking-Setup-3.2.0.exe': b'fixture installer', 'dependencies.txt': b'fixture',
                 'size-report.json': '{}', 'desktop-package-lock.json': '{}'}
        if mutate:
            mutate(files)
        path = self.root / ('bundle-' + str(time.monotonic_ns()) + '.zip')
        path.write_bytes(zipped({row['target'] + '/' + name: value for name, value in files.items()}))
        return path, row

    def test_candidate_staging_verifies_signature_provenance_inner_build_and_installer(self):
        path, row = self.signed_bundle()
        result = gate.stage_candidate(path, self.root / 'staged', row, self.plan)
        self.assertEqual(result['version'], '3.2.0')
        self.assertEqual((self.root / 'staged/release-3.2.0.zip').read_bytes(),
                         (self.root / 'staged/setup-payload/release.zip').read_bytes())

    def test_candidate_tamper_untrusted_keys_and_provenance_mismatch_fail(self):
        mutations = [lambda files: files.update({'SOFT-Tracking-Setup-3.2.0.exe': b'changed'}),
                     lambda files: files.update({'release-3.2.0.zip': b'changed'}),
                     lambda files: files.update({'provenance.json': json.dumps(dict(json.loads(files['provenance.json']), commit='c' * 40))}),
                     lambda files: files.update({'provenance.json': json.dumps(dict(json.loads(files['provenance.json']), run_url='https://example.test/'))}),
                     lambda files: files.update({'../unlisted': b'private'})]
        for index, mutation in enumerate(mutations):
            path, row = self.signed_bundle(mutation)
            with self.subTest(index=index), self.assertRaises(ValueError):
                gate.stage_candidate(path, self.root / ('failed-' + str(index)), row, self.plan)
        path, row = self.signed_bundle()
        with self.assertRaises(ValueError):
            gate.stage_candidate(path, self.root / 'untrusted', row, dict(self.plan, trusted_keys={}))

    def promotion_api(self, corrupt_remote=False, state=None):
        api = Mock(repo=self.repo)
        state = state if state is not None else {'tag': None, 'release': None, 'assets': {}}
        calls = []
        def release_fixture():
            if state['release'] is None:
                return None
            release = self.release(state['assets'])
            release.update(state['release'])
            if corrupt_remote and release['assets']:
                release['assets'][0]['digest'] = 'sha256:' + '0' * 64
            return release
        def call(path, method='GET', body=None, absent=False):
            calls.append((path, method, body))
            if path == 'git/ref/tags/v3.2.0':
                return copy.deepcopy(state['tag'])
            if path == 'releases/tags/v3.2.0':
                return None if state.get('draft_list_only') else release_fixture()
            if path == 'releases?per_page=100&page=1':
                return [release_fixture()] if state['release'] is not None else []
            if path == 'git/refs':
                self.assertEqual(body, {'ref': 'refs/tags/v3.2.0', 'sha': 'a' * 40})
                self.assertIsNone(state['tag'])
                state['tag'] = {'object': {'type': 'commit', 'sha': 'a' * 40}}
                return {}
            if path == 'releases':
                self.assertIsNotNone(state['tag'])
                self.assertIsNone(state['release'])
                self.assertFalse(body['prerelease'])
                self.assertTrue(body['draft'])
                state['release'] = dict(body, id=99)
                return {'id': 99}
            if path == 'releases/99' and method == 'GET':
                return release_fixture()
            if path == 'releases/99' and method == 'PATCH':
                state['release'].update(body)
                return release_fixture()
            self.fail('Unexpected API path')
        def upload(release, path):
            self.assertEqual(release, 99)
            self.assertEqual(path.read_bytes(), self.contents[path.name])
            self.assertNotIn(path.name, state['assets'])
            if len(state['assets']) == state.get('fail_after'):
                raise ConnectionError('Fictional interrupted upload')
            state['assets'][path.name] = dict(self.plan['assets'][path.name], id=self.plan['assets'][path.name]['id'] + 1000)
        def asset(identifier, destination, expected, size):
            name = next(name for name, value in self.plan['assets'].items() if identifier in (value['id'], value['id'] + 1000))
            require = self.plan['assets'][name]
            self.assertEqual((expected, size), (require['sha256'], require['size']))
            destination.write_bytes(self.contents[name])
        api.api.side_effect = call
        api.upload.side_effect = upload
        api.asset.side_effect = asset
        return api, calls

    def test_promotion_copies_unchanged_bytes_and_rechecks_every_remote_download(self):
        api, calls = self.promotion_api()
        def stage(_path, destination, *_args):
            destination.mkdir()
        with patch.object(gate, 'GitHub', return_value=api), patch.object(gate, 'authorize_dispatch'), \
                patch.object(gate, 'accepted_jobs') as accepted, patch.object(gate, 'revalidate') as validate, \
                patch.object(gate, 'stage_candidate', side_effect=stage):
            gate.promote()
        accepted.assert_called_once()
        self.assertEqual(validate.call_count, 2)
        self.assertEqual(api.upload.call_count, 16)
        self.assertEqual(api.asset.call_count, 32)
        path, method, body = calls[-1]
        self.assertEqual((path, method), ('releases/99', 'PATCH'))
        self.assertEqual({key: body[key] for key in ('draft', 'prerelease', 'make_latest')},
                         {'draft': False, 'prerelease': False, 'make_latest': 'false'})
        self.assertIn(self.plan['harness_sha'], body['body'])
        self.assertIn(self.plan['candidate_sha'], body['body'])

    def test_promotion_never_publishes_corrupt_upload(self):
        api, calls = self.promotion_api(corrupt_remote=True)
        with patch.object(gate, 'GitHub', return_value=api), patch.object(gate, 'authorize_dispatch'), \
                patch.object(gate, 'accepted_jobs'), patch.object(gate, 'revalidate'), \
                patch.object(gate, 'stage_candidate', side_effect=lambda _path, destination, *_: destination.mkdir()):
            with self.assertRaises(ValueError):
                gate.promote()
        self.assertFalse(any(method == 'PATCH' for _, method, _ in calls))

    def test_interrupted_promotion_resumes_matching_draft_after_new_full_acceptance(self):
        state = {'tag': None, 'release': None, 'assets': {}, 'fail_after': 3, 'draft_list_only': True}
        api, calls = self.promotion_api(state=state)
        with patch.object(gate, 'GitHub', return_value=api), patch.object(gate, 'authorize_dispatch'), \
                patch.object(gate, 'accepted_jobs') as accepted, patch.object(gate, 'revalidate'), \
                patch.object(gate, 'stage_candidate', side_effect=lambda _path, destination, *_: destination.mkdir()):
            with self.assertRaises(ConnectionError):
                gate.promote()
            self.assertTrue(state['release']['draft'])
            self.assertEqual(len(state['assets']), 3)
            self.assertFalse(any(method == 'PATCH' for _, method, _ in calls))
            already_uploaded = set(state['assets'])
            gate.cleanup()
            del state['fail_after']
            api.upload.reset_mock()
            api.asset.reset_mock()
            calls.clear()
            new_plan = dict(self.plan, acceptance_run_id=789, harness_sha='c' * 40)
            with patch.dict(os.environ, {'GITHUB_RUN_ID': '789', 'GITHUB_SHA': 'c' * 40,
                                        'ACCEPTANCE_PLAN': json.dumps(new_plan)}):
                gate.promote()
        self.assertEqual(accepted.call_count, 2)
        self.assertEqual(accepted.call_args.args[1], new_plan)
        self.assertFalse(state['release']['draft'])
        self.assertEqual(set(state['assets']), set(self.plan['assets']))
        self.assertEqual(api.upload.call_count, 13)
        self.assertTrue(all(call.args[1].name not in already_uploaded for call in api.upload.call_args_list))
        self.assertEqual(api.asset.call_count, 3 + 16 + 16)
        verified_first = [call.args[0] for call in api.asset.call_args_list[:3]]
        self.assertEqual(set(verified_first), {state['assets'][name]['id'] for name in already_uploaded})
        self.assertFalse(any(method in ('POST', 'DELETE') for _, method, _ in calls))
        self.assertEqual(calls[-1][:2], ('releases/99', 'PATCH'))
        self.assertIn('/actions/runs/789/attempts/1', calls[-1][2]['body'])
        self.assertIn('Harness: ' + 'c' * 40, calls[-1][2]['body'])

    def test_resume_rejects_extra_mismatch_incomplete_published_and_moved_tag_without_writes(self):
        for change in ('extra', 'hash', 'size', 'published', 'moved_tag', 'missing_tag'):
            name = next(iter(self.plan['assets']))
            state = {'tag': {'object': {'type': 'commit', 'sha': 'a' * 40}},
                     'release': {'id': 99, 'tag_name': 'v3.2.0', 'draft': True, 'prerelease': False},
                     'assets': {name: dict(self.plan['assets'][name], id=1100)}}
            if change == 'extra':
                state['assets']['unapproved.zip'] = dict(state['assets'][name], id=999)
            elif change == 'hash':
                state['assets'][name]['sha256'] = '0' * 64
            elif change == 'size':
                state['assets'][name]['size'] += 1
            elif change == 'published':
                state['release']['draft'] = False
            elif change == 'moved_tag':
                state['tag']['object']['sha'] = 'd' * 40
            else:
                state['tag'] = None
            api, calls = self.promotion_api(state=state)
            with patch.object(gate, 'GitHub', return_value=api), patch.object(gate, 'authorize_dispatch'), \
                    patch.object(gate, 'accepted_jobs'), patch.object(gate, 'revalidate'), \
                    patch.object(gate, 'stage_candidate', side_effect=lambda _path, destination, *_: destination.mkdir()):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    gate.promote()
            self.assertTrue(all(method == 'GET' for _, method, _ in calls))
            api.upload.assert_not_called()
            gate.cleanup()

    def test_resume_matching_orphan_tag_or_empty_draft_does_not_force_or_recreate_tag(self):
        for draft in (None, {'id': 99, 'tag_name': 'v3.2.0', 'draft': True, 'prerelease': False}):
            state = {'tag': {'object': {'type': 'commit', 'sha': 'a' * 40}}, 'release': draft, 'assets': {}}
            api, calls = self.promotion_api(state=state)
            with patch.object(gate, 'GitHub', return_value=api), patch.object(gate, 'authorize_dispatch'), \
                    patch.object(gate, 'accepted_jobs'), patch.object(gate, 'revalidate'), \
                    patch.object(gate, 'stage_candidate', side_effect=lambda _path, destination, *_: destination.mkdir()):
                gate.promote()
            self.assertFalse(any(path == 'git/refs' or method == 'DELETE' for path, method, _ in calls))
            self.assertEqual(api.upload.call_count, 16)
            gate.cleanup()

    def test_cleanup_removes_only_owned_runner_temp(self):
        root = gate.private_root()
        root.mkdir()
        (root / 'private.log').write_text('fixture')
        other = self.root / 'unrelated'
        other.write_text('keep')
        gate.cleanup()
        self.assertFalse(root.exists())
        self.assertEqual(other.read_text(), 'keep')

    def test_smoke_imports_candidate_code_with_distinct_harness_location(self):
        candidate = self.root / 'candidate'
        shutil.copytree(ROOT / 'agent/agent_tracker', candidate / 'agent/agent_tracker',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        code = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                'import smoke_update; import agent_tracker.installer as installer; '
                'assert Path(installer.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve()); '
                'assert smoke_update.ROOT != smoke_update.CLIENT_ROOT')
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'tools'), str(candidate)],
                                env=dict(os.environ, TRACKING_SMOKE_CLIENT_ROOT=str(candidate)),
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


if __name__ == '__main__':
    unittest.main()
