"""Gate regression fixtures only: no real downloads, native builds or acceptance."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from tools import ci
import smoke_published
from agent_tracker.core.files import atomic_json


class CandidatePlanTests(unittest.TestCase):
    def test_normal_version_remains_stable_without_removing_private_baselines(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            with patch.dict(os.environ, {'RELEASE_VERSION': '3.2.0', 'RELEASE_TARGET': 'all',
                                         'RELEASE_UPGRADE_FROM': '', 'GITHUB_OUTPUT': str(output)}):
                ci.plan()
            result = dict(line.split('=', 1) for line in output.read_text().splitlines())
        self.assertEqual(result['version'], '3.2.0')
        self.assertFalse(ci.Version(result['version']).is_prerelease)
        self.assertEqual(result['upgrade_from'], '3.1.0,3.0.4')
        rows = json.loads(result['matrix'])['include']
        self.assertEqual(rows, ci.TARGETS)
        self.assertEqual(len({row['target'] for row in rows}), 8)
        self.assertEqual(sum(row['ui'] == 'qt' for row in rows), 2)


class PublishedGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = 'windows-x64-modern'
        self.current = self.root / 'artifacts' / self.target / '3.2.0'
        self.keys = {'fixture-only': 'not-a-real-signing-key'}
        atomic_json(self.current / 'setup-payload/setup-build.json', {'os': 'windows', 'architecture': 'x64'})
        atomic_json(self.current / 'setup-payload/trusted-update-keys.json', self.keys)
        self.archives, self.manifests, pins = {}, {}, {}
        for number, old in enumerate(('3.1.0', '3.0.4'), 100):
            raw = b'fixture baseline, never executed'
            envelope = {'fixture_version': old}
            self.manifests[old] = {'version': old, 'target': self.target, 'size': len(raw),
                                   'sha256': hashlib.sha256(raw).hexdigest()}
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr(self.target + '/manifest.json', json.dumps(envelope))
                archive.writestr(self.target + '/release-' + old + '.zip', raw)
            self.archives[str(number)] = buffer.getvalue()
            pins[old] = {self.target: [number, hashlib.sha256(buffer.getvalue()).hexdigest()]}
        atomic_json(self.root / 'tools/upgrade-baselines.json', pins)
        self.acceptance = []
        for patcher in (
            patch.object(smoke_published, 'ROOT', self.root),
            patch.object(smoke_published, 'target', return_value=self.target),
            patch.object(smoke_published, 'version', return_value='3.2.0'),
            patch.dict(os.environ, {'GITHUB_REPOSITORY': 'fixture/private-gates', 'RELEASE_UPGRADE_FROM': ''}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_fixture(self, command, **options):
        self.assertTrue(options['check'])
        if command[0] == 'gh':
            self.assertEqual(command[:2], ['gh', 'api'])
            self.assertTrue(command[2].startswith('repos/fixture/private-gates/releases/assets/'))
            options['stdout'].write(self.archives[command[2].rsplit('/', 1)[1]])
        else:
            self.assertEqual(command[:2], [sys.executable, str(self.root / 'tools/smoke_update.py')])
            self.assertEqual(command[-2:], [str(self.current), '--published'])
            self.acceptance.append(Path(command[2]).name)

    def verified_fixture(self, envelope, keys, minimum, system, architecture):
        self.assertEqual((keys, minimum, system, architecture), (self.keys, '0.0.0', 'windows', 'x64'))
        return self.manifests[envelope['fixture_version']]

    def test_empty_input_still_executes_both_required_real_baseline_paths(self):
        with patch.object(smoke_published.subprocess, 'run', side_effect=self.run_fixture), \
                patch.object(smoke_published, 'verify_manifest', side_effect=self.verified_fixture) as verify:
            smoke_published.main()
        self.assertEqual(self.acceptance, ['3.1.0', '3.0.4'])
        self.assertEqual(verify.call_count, 2)

    def test_missing_private_baseline_fails_without_synthetic_fallback(self):
        with patch.object(smoke_published.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['gh'])) as run, \
                patch.object(smoke_published, 'verify_manifest') as verify:
            with self.assertRaises(subprocess.CalledProcessError):
                smoke_published.main()
        self.assertEqual(run.call_count, 1)
        verify.assert_not_called()
        self.assertEqual(self.acceptance, [])

    def test_checksum_failure_stops_before_signature_or_native_acceptance(self):
        self.archives['100'] = b'wrong old bytes'
        with patch.object(smoke_published.subprocess, 'run', side_effect=self.run_fixture), \
                patch.object(smoke_published, 'verify_manifest') as verify:
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                smoke_published.main()
        verify.assert_not_called()
        self.assertEqual(self.acceptance, [])

    def test_invalid_signature_or_expiry_does_not_count_as_acceptance(self):
        with patch.object(smoke_published.subprocess, 'run', side_effect=self.run_fixture), \
                patch.object(smoke_published, 'verify_manifest', side_effect=ValueError('invalid signed baseline')):
            with self.assertRaisesRegex(ValueError, 'invalid signed baseline'):
                smoke_published.main()
        self.assertEqual(self.acceptance, [])

    def test_relabelled_synthetic_version_is_not_a_published_baseline(self):
        self.manifests['3.1.0']['version'] = '0.0.1'
        with patch.object(smoke_published.subprocess, 'run', side_effect=self.run_fixture), \
                patch.object(smoke_published, 'verify_manifest', side_effect=self.verified_fixture):
            with self.assertRaisesRegex(ValueError, 'Wrong published baseline'):
                smoke_published.main()
        self.assertEqual(self.acceptance, [])

    def test_wrong_target_is_not_a_published_baseline(self):
        self.manifests['3.1.0']['target'] = 'linux-x64-modern'
        with patch.object(smoke_published.subprocess, 'run', side_effect=self.run_fixture), \
                patch.object(smoke_published, 'verify_manifest', side_effect=self.verified_fixture):
            with self.assertRaisesRegex(ValueError, 'Wrong published baseline'):
                smoke_published.main()
        self.assertEqual(self.acceptance, [])

    def test_passing_one_baseline_does_not_excuse_a_missing_second_baseline(self):
        def run(command, **options):
            if command[0] == 'gh' and command[2].endswith('/101'):
                raise subprocess.CalledProcessError(1, command)
            return self.run_fixture(command, **options)
        with patch.object(smoke_published.subprocess, 'run', side_effect=run), \
                patch.object(smoke_published, 'verify_manifest', side_effect=self.verified_fixture):
            with self.assertRaises(subprocess.CalledProcessError):
                smoke_published.main()
        self.assertEqual(self.acceptance, ['3.1.0'])

    def test_native_gate_failure_propagates_without_export_or_success(self):
        def run(command, **options):
            if command[0] != 'gh':
                raise subprocess.CalledProcessError(1, command)
            return self.run_fixture(command, **options)
        with patch.object(smoke_published.subprocess, 'run', side_effect=run), \
                patch.object(smoke_published, 'verify_manifest', side_effect=self.verified_fixture):
            with self.assertRaises(subprocess.CalledProcessError):
                smoke_published.main()
        self.assertEqual(self.acceptance, [])


if __name__ == '__main__':
    unittest.main()
