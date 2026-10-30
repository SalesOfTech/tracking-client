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
    def test_candidate_identity_uses_selected_stable_version(self):
        with patch.object(gate, 'VERSION', '3.2.1'):
            self.assertEqual(gate.candidate_identity('candidate-3.2.1-123-2'), (123, 2))
            with self.assertRaises(ValueError):
                gate.candidate_identity('candidate-3.2.0-123-2')

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
        for field, value in (('run_attempt', 1), ('conclusion', 'failure'), ('event', 'pull_request'),
                             ('head_branch', 'feature'), ('workflow_id', 88), ('path', 'other.yml')):
            original = data['actions/runs/123'][field]
            data['actions/runs/123'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.candidate_snapshot(api, self.tag)
            data['actions/runs/123'][field] = original
        data['git/ref/tags/' + self.tag]['object']['sha'] = 'c' * 40
        with self.assertRaises(ValueError):
            gate.candidate_snapshot(api, self.tag)

    def test_candidate_same_sha_failed_job_retry_retains_successful_targets(self):
        api, data = self.candidate_api()
        initial = copy.deepcopy(data['actions/runs/123'])
        initial['conclusion'] = 'cancelled'
        data['actions/runs/123']['run_attempt'] = 3
        data['actions/runs/123/attempts/2'] = initial
        data['actions/runs/123/attempts/3'] = copy.deepcopy(data['actions/runs/123'])
        old = [dict(job, run_id=123, head_sha='a' * 40, status='completed')
               for job in api.jobs.return_value]
        old[0]['conclusion'] = 'cancelled'
        old[-1]['conclusion'] = 'skipped'
        retried = [dict(old[0], conclusion='success'), dict(old[-1], conclusion='success')]
        api.jobs.side_effect = lambda run, attempt: copy.deepcopy(old if attempt == 2 else retried)
        result = gate.candidate_snapshot(api, self.tag)
        self.assertEqual(result['candidate_run_attempt'], 3)
        self.assertEqual(result['candidate_tag'], self.tag)
        for change in ('wrong_sha', 'wrong_run', 'failed', 'missing', 'duplicate', 'changed_attempt_sha'):
            saved = copy.deepcopy(retried)
            if change == 'wrong_sha':
                retried[0]['head_sha'] = 'c' * 40
            elif change == 'wrong_run':
                retried[0]['run_id'] = 987
            elif change == 'failed':
                retried[0]['conclusion'] = 'failure'
            elif change == 'missing':
                retried.pop(0)
            elif change == 'duplicate':
                retried.append(retried[0])
            else:
                data['actions/runs/123/attempts/2']['head_sha'] = 'c' * 40
            with self.subTest(change=change), self.assertRaises(ValueError):
                gate.candidate_snapshot(api, self.tag)
            retried[:] = saved
            data['actions/runs/123/attempts/2']['head_sha'] = 'a' * 40

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

    def urls(self, versions=('3.1.0', '3.0.4')):
        return {old: {row['target']: {'url': 'https://release-assets.githubusercontent.com/fixture?sig=not-real'}
                      for row in gate.TARGETS} for old in versions}

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

    def test_seven_baselines_accept_private_only_or_full_url_map(self):
        with patch.object(gate, 'VERSION', '3.5.0'):
            versions = gate.upgrade_baselines(gate.VERSION)
            self.assertEqual(len(versions), 7)
            for urls in (self.urls(), self.urls(versions)):
                self.assertEqual(gate.baseline_urls(json.dumps(urls)), urls)
            for urls in (self.urls(['3.1.0']), self.urls(['3.1.0', '3.0.4', '3.2.0']),
                         self.urls(versions + ['9.9.9']), self.urls(versions[:-1])):
                with self.subTest(versions=list(urls)), self.assertRaises(ValueError):
                    gate.baseline_urls(json.dumps(urls))
            urls = self.urls(versions)
            urls['3.4.2'][gate.TARGETS[0]['target']]['url'] = 'https://evil.test/a?x=1'
            with self.assertRaises(ValueError):
                gate.baseline_urls(json.dumps(urls))
            with self.assertRaises(ValueError):
                gate.baseline_urls(' ' * (48 * 1024) + json.dumps(self.urls()))

    def test_public_baseline_uses_pinned_id_digest_and_exact_metadata_size(self):
        row = gate.TARGETS[0]
        api = Mock()
        for version in sorted(gate.PUBLIC_BASELINES):
            metadata = dict(id=123, name=f'SOFT-Tracking-{row["target"]}-{version}.zip',
                            state='uploaded', digest='sha256:' + 'a' * 64, size=1234)
            api.api.return_value = metadata
            destination = self.root / (version + '.zip')
            with patch.object(gate, 'download_url') as delegated:
                gate.download_baseline(api, version, row['target'], [123, 'a' * 64],
                                       self.urls(gate.upgrade_baselines('3.5.0')), destination)
            api.api.assert_called_with('releases/assets/123')
            api.asset.assert_called_with(123, destination, 'a' * 64, 1234)
            delegated.assert_not_called()

    def test_current_release_requires_350_and_automatic_supervisor_acceptance(self):
        with patch.object(gate, 'VERSION', '3.5.1'):
            versions = gate.upgrade_baselines(gate.VERSION)
            self.assertEqual(len(versions), 8)
            self.assertEqual(versions[0], '3.5.0')
            self.assertEqual(gate.baseline_urls(json.dumps(self.urls())), self.urls())
            with patch.object(gate, 'read_plan', return_value=self.plan), \
                    patch.object(gate, 'row_for_target', return_value=gate.TARGETS[0]), \
                    patch.object(gate, 'check_native'), \
                    patch.object(gate, 'private_root', return_value=self.root), \
                    patch.object(gate.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
                gate.test()
            self.assertEqual(run.call_count, 10)
            automatic = [call for call in run.call_args_list if 'smoke_auto_update.py' in call.args[0][1]]
            self.assertEqual([call.args[0][2] for call in automatic],
                             [str(self.root / version) for version in ('3.5.0', '3.4.2')])
            self.assertTrue(all(call.kwargs['check'] for call in automatic))

    def test_automatic_failure_exposes_only_known_stage(self):
        path = self.root / 'private.log'
        path.write_text('FAIL: automatic-update stage=automatic-transition exception=RuntimeError\n')
        self.assertEqual(gate.automatic_failure_stage(path), 'automatic-transition')
        path.write_text('FAIL: automatic-update stage=private-secret exception=RuntimeError\n')
        self.assertEqual(gate.automatic_failure_stage(path), 'subprocess-failure')

    def test_public_metadata_mismatch_never_downloads_or_falls_back(self):
        target = gate.TARGETS[0]['target']
        metadata = dict(id=123, name=f'SOFT-Tracking-{target}-3.4.2.zip',
                        state='uploaded', digest='sha256:' + 'a' * 64, size=1234)
        for change in ({'id': 124}, {'id': True}, {'name': 'wrong.zip'}, {'state': 'new'},
                       {'digest': None}, {'digest': 'sha256:' + 'b' * 64},
                       {'size': 0}, {'size': True}, {'size': gate.MAX_BUNDLE + 1}):
            api = Mock()
            api.api.return_value = dict(metadata, **change)
            with self.subTest(change=change), patch.object(gate, 'download_url') as delegated:
                with self.assertRaises(ValueError):
                    gate.download_baseline(api, '3.4.2', target, [123, 'a' * 64],
                                           self.urls(['3.4.2']), self.root / 'download')
                api.asset.assert_not_called()
                delegated.assert_not_called()
        for failing_method in ('api', 'asset'):
            api = Mock()
            api.api.return_value = metadata
            getattr(api, failing_method).side_effect = ValueError('unavailable or corrupted asset')
            with self.subTest(failing_method=failing_method), patch.object(gate, 'download_url') as delegated:
                with self.assertRaises(ValueError):
                    gate.download_baseline(api, '3.4.2', target, [123, 'a' * 64],
                                           self.urls(['3.4.2']), self.root / 'download')
                delegated.assert_not_called()

    def test_private_baselines_use_delegated_urls_without_github_access(self):
        target = gate.TARGETS[0]['target']
        for version in ('3.1.0', '3.0.4'):
            api = Mock()
            with patch.object(gate, 'download_url') as delegated:
                gate.download_baseline(api, version, target, [123, 'a' * 64], self.urls(), self.root / 'download')
            delegated.assert_called_once_with(self.urls()[version][target]['url'], self.root / 'download', 'a' * 64)
            api.api.assert_not_called()
            api.asset.assert_not_called()

    def test_invalid_baseline_pins_fail_before_network(self):
        for pin in ([], [123], [True, 'a' * 64], [0, 'a' * 64], [123, 'bad'], [123, 'a' * 64, 'extra']):
            api = Mock()
            with self.subTest(pin=pin), patch.object(gate, 'download_url') as delegated:
                with self.assertRaises(ValueError):
                    gate.download_baseline(api, '3.4.2', gate.TARGETS[0]['target'], pin, {}, self.root / 'download')
                api.api.assert_not_called()
                delegated.assert_not_called()

    def test_all_56_baselines_still_download_stage_and_run_actual_upgrade_command(self):
        pins = json.loads((ROOT / 'tools/upgrade-baselines.json').read_text())
        versions = gate.upgrade_baselines('3.5.0')
        for row in gate.TARGETS:
            root = self.root / row['target']
            name = f'SOFT-Tracking-{row["target"]}-3.5.0.zip'
            plan = dict(self.plan, version='3.5.0', assets={
                name: {'id': 1, 'sha256': 'a' * 64, 'size': 10},
                name + '.sha256': {'id': 2, 'sha256': 'b' * 64, 'size': 100}})
            api = Mock()
            metadata = {pins[old][row['target']][0]: {
                'id': pins[old][row['target']][0], 'name': f'SOFT-Tracking-{row["target"]}-{old}.zip',
                'size': 1234, 'state': 'uploaded', 'digest': 'sha256:' + pins[old][row['target']][1]}
                for old in gate.PUBLIC_BASELINES}
            api.api.side_effect = lambda suffix: metadata[int(suffix.rsplit('/', 1)[1])]

            def asset(identifier, destination, expected, size):
                if identifier == 2:
                    destination.write_text('a' * 64 + '  ' + name, encoding='ascii')

            api.asset.side_effect = asset
            with self.subTest(target=row['target']), patch.object(gate, 'VERSION', '3.5.0'), \
                    patch.object(gate, 'read_plan', return_value=plan), \
                    patch.object(gate, 'row_for_target', return_value=row), \
                    patch.object(gate, 'check_native'), patch.object(gate, 'revalidate'), \
                    patch.object(gate, 'GitHub', return_value=api), \
                    patch.object(gate, 'private_root', return_value=root), \
                    patch.dict(os.environ, TRACKING_BASELINE_URLS=json.dumps(self.urls())), \
                    patch.object(gate, 'download_url') as delegated, \
                    patch.object(gate, 'stage_candidate') as current, \
                    patch.object(gate, 'stage_published_archive') as stage, \
                    patch.object(gate.subprocess, 'run') as run, redirect_stdout(io.StringIO()):
                gate.download()
                self.assertNotIn('TRACKING_BASELINE_URLS', os.environ)
                self.assertEqual(api.api.call_count, 5)
                self.assertEqual(api.asset.call_count, 7)  # Five baselines plus candidate/checksum.
                self.assertEqual(delegated.call_count, 2)
                self.assertEqual([call.args[1] for call in delegated.call_args_list],
                                 [root / (old + '.zip') for old in ('3.1.0', '3.0.4')])
                current.assert_called_once()
                self.assertEqual([call.args[2] for call in stage.call_args_list], versions)
                self.assertTrue(all(call.args[3:5] == (row['target'], self.keys) for call in stage.call_args_list))
                gate.test()
                self.assertEqual(run.call_count, 7)
                self.assertEqual([call.args[0][2] for call in run.call_args_list], [str(root / old) for old in versions])
                self.assertTrue(all(call.args[0][-1] == '--published' and call.kwargs['check'] for call in run.call_args_list))

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
                                            (200, b'fixture', hashlib.sha256(b'fixture').hexdigest(), 2),
                                            (200, b'fixture', hashlib.sha256(b'fixture').hexdigest(), 8),
                                            (200, b'', hashlib.sha256(b'').hexdigest(), None))):
            response = Mock(status_code=status)
            response.iter_content.return_value = [data]
            path = self.root / ('download-' + str(index))
            with self.assertRaises(ValueError):
                gate.receive(response, path, expected, size)

    def test_github_asset_redirect_keeps_token_on_api_and_verifies_exact_bytes(self):
        expected = hashlib.sha256(b'fixture').hexdigest()
        delegated = self.urls()['3.1.0'][gate.TARGETS[0]['target']]['url']
        responses = [Mock(status_code=302, headers={'Location': delegated}), Mock(status_code=200)]
        responses[1].iter_content.return_value = [b'fixture']
        sessions = []
        for response in responses:
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            session = Mock()
            session.get.return_value = response
            session.__enter__ = Mock(return_value=session)
            session.__exit__ = Mock(return_value=False)
            sessions.append(session)
        with patch.dict(os.environ, GH_TOKEN='fictional-token'), \
                patch.object(gate.requests, 'Session', side_effect=sessions):
            gate.GitHub().asset(123, self.root / 'public.zip', expected, 7)
        self.assertEqual(gate.digest(self.root / 'public.zip'), expected)
        self.assertEqual(sessions[0].get.call_args.args[0],
                         f'https://api.github.com/repos/{self.repo}/releases/assets/123')
        self.assertEqual(sessions[0].get.call_args.kwargs['headers']['Authorization'], 'Bearer fictional-token')
        self.assertEqual(sessions[1].get.call_args.args[0], delegated)
        self.assertNotIn('headers', sessions[1].get.call_args.kwargs)
        self.assertNotIn('auth', sessions[1].get.call_args.kwargs)
        self.assertTrue(all(not session.trust_env and not session.get.call_args.kwargs['allow_redirects']
                            for session in sessions))

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

    def test_windows_smoke_preserves_native_profile_but_isolates_writable_paths(self):
        host = {'USERPROFILE': r'C:\Users\fixture', 'HOME': r'C:\Users\fixture',
                'APPDATA': r'C:\Users\fixture\AppData\Roaming',
                'LOCALAPPDATA': r'C:\Users\fixture\AppData\Local', 'TEMP': r'C:\host-temp',
                'TRACKING_BASELINE_URLS': 'private-value', 'GH_TOKEN': 'private-value',
                'GITHUB_TOKEN': 'private-value', 'ACTIONS_RUNTIME_TOKEN': 'private-value',
                'SSH_AUTH_SOCK': 'private-value', 'NODE_OPTIONS': 'private-value',
                'PYTHONPATH': 'private-value', 'PYTHONOPTIMIZE': '1', 'ELECTRON_ENABLE_LOGGING': '1'}
        with patch.dict(os.environ, host, clear=True), patch.object(gate.sys, 'platform', 'win32'):
            env = gate.smoke_environment(self.root)
        self.assertEqual(env['USERPROFILE'], host['USERPROFILE'])
        for name in ('HOME', 'APPDATA', 'LOCALAPPDATA'):
            self.assertEqual(env[name], str(self.root / 'home'))
        for name in ('TMP', 'TEMP', 'TMPDIR'):
            self.assertEqual(env[name], str(self.root / 'tmp'))
        self.assertNotIn('private-value', env.values())
        self.assertNotIn('PYTHONOPTIMIZE', env)
        self.assertNotIn('ELECTRON_ENABLE_LOGGING', env)
        self.assertEqual(env['TRACKING_SMOKE_CLIENT_ROOT'], str(gate.ROOT / 'candidate'))
        self.assertTrue((self.root / 'home').is_dir())
        self.assertTrue((self.root / 'tmp').is_dir())

    def test_windows_smoke_requires_native_profile_and_never_invents_one(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(gate.sys, 'platform', 'win32'), \
                self.assertRaises(ValueError):
            gate.smoke_environment(self.root)

    def test_posix_smoke_keeps_isolated_home_without_windows_profile(self):
        for platform in ('linux', 'darwin'):
            with patch.dict(os.environ, {'USERPROFILE': 'host-profile', 'HOME': 'host-home'}, clear=True), \
                    patch.object(gate.sys, 'platform', platform):
                env = gate.smoke_environment(self.root)
            self.assertEqual(env['HOME'], str(self.root / 'home'))
            self.assertNotIn('USERPROFILE', env)
            self.assertNotIn('host-profile', env.values())

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

    def test_new_release_requires_extra_macos_native_acceptance(self):
        api = Mock()
        good = [{'name': 'accept-' + row['target'], 'status': 'completed', 'conclusion': 'success',
                 'steps': [{'name': gate.SMOKE_STEP, 'conclusion': 'success'}]} for row in gate.TARGETS]
        extra = [{'name': name, 'status': 'completed', 'conclusion': 'success',
                  'steps': [{'name': gate.SMOKE_STEP, 'conclusion': 'success'}]}
                 for name in ('verify-macos-15-arm64', 'verify-macos-26-arm64')]
        with patch.object(gate, 'VERSION', '3.5.1'):
            api.jobs.return_value = good + extra
            gate.accepted_jobs(api, self.plan)
            for change in ('missing', 'duplicate', 'failed', 'failed_step'):
                rows = copy.deepcopy(extra)
                if change == 'missing':
                    rows.pop()
                elif change == 'duplicate':
                    rows[1] = rows[0]
                elif change == 'failed':
                    rows[0]['conclusion'] = 'failure'
                else:
                    rows[0]['steps'][0]['conclusion'] = 'failure'
                api.jobs.return_value = good + rows
                with self.subTest(change=change), self.assertRaises(ValueError):
                    gate.accepted_jobs(api, self.plan)

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

    def test_incomplete_job_list_needs_exact_successful_job_detail(self):
        api = Mock()
        rows = [{'id': i + 1, 'name': 'accept-' + row['target'], 'status': 'completed', 'conclusion': 'success',
                 'steps': [{'name': gate.SMOKE_STEP, 'conclusion': 'success'}]} for i, row in enumerate(gate.TARGETS)]
        detail = dict(copy.deepcopy(rows[0]), run_id=self.plan['acceptance_run_id'])
        rows[0]['steps'][0]['conclusion'] = None
        api.jobs.return_value = rows
        api.api.return_value = detail
        gate.accepted_jobs(api, self.plan)
        api.api.assert_called_once_with('actions/jobs/1')
        for change in ('failure', 'wrong_run'):
            bad = copy.deepcopy(detail)
            if change == 'failure':
                bad['steps'][0]['conclusion'] = 'failure'
            else:
                bad['run_id'] += 1
            api.api.return_value = bad
            with self.assertRaises(ValueError):
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

    def test_candidate_migrator_metadata_and_bytes_are_verified(self):
        def add(files, bad=False):
            files['SOFT-Tracking-Migrate-3.2.0.exe'] = b'fixture migrator'
            files['migration.json'] = json.dumps({
                'file': 'SOFT-Tracking-Migrate-3.2.0.exe', 'version': '3.2.0',
                'target': gate.TARGETS[0]['target'],
                'sha256': hashlib.sha256(b'wrong' if bad else b'fixture migrator').hexdigest()})
        path, row = self.signed_bundle(add)
        gate.stage_candidate(path, self.root / 'migration-valid', row, self.plan)
        path, row = self.signed_bundle(lambda files: add(files, True))
        with self.assertRaises(ValueError):
            gate.stage_candidate(path, self.root / 'migration-invalid', row, self.plan)

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
