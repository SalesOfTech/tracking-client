"""Fixture-only export checks; no native build, network, publication or enrollment."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from tools import ci
from tools import smoke_package


class MigrationPackagingTests(unittest.TestCase):
    def test_frozen_self_test_supplies_required_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / 'bundle'
            bundle.mkdir()
            (bundle / 'setup-build.json').write_text(json.dumps({'os': 'windows'}))
            (root / 'migration.json').write_text(json.dumps({'file': 'SOFT-Tracking-Migrate.exe'}))
            with patch.object(smoke_package, 'run_frozen', autospec=True) as run:
                run.return_value = CompletedProcess([], 0, b'PASS: isolated frozen migration smoke', b'')
                # Stop before the independent package/GUI smoke: this fixture has no guide.
                with self.assertRaisesRegex(AssertionError, 'Missing offline setup guide'):
                    smoke_package.main(bundle)
                run.assert_called_once_with([str(root / 'SOFT-Tracking-Migrate.exe'), '--self-test'],
                                            env=dict(os.environ), timeout=180)

    def export_fixture(self, target, version, valid_hash=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        build = root / 'artifacts' / target / version
        (build / 'payload').mkdir(parents=True)
        (build / 'payload' / 'build.json').write_text(json.dumps({'ui': 'qt'}))
        for name in ('manifest.json', 'release-' + version + '.zip', 'dependencies.txt', 'size-report.json', 'setup.exe'):
            (build / name).write_bytes(b'fixture')
        (build / 'setup.json').write_text(json.dumps(dict(file='setup.exe', version=version, target=target)))
        if target.startswith('windows-'):
            (build / 'SOFT-Tracking-Migrate.exe').write_bytes(b'migration fixture')
            (build / 'migration.json').write_text(json.dumps(dict(file='SOFT-Tracking-Migrate.exe',
                version=version, target=target, sha256=hashlib.sha256(b'migration fixture').hexdigest() if valid_hash else 'wrong')))
        env = dict(RELEASE_VERSION=version, RELEASE_TARGET=target, GITHUB_SHA='fixture',
                   GITHUB_SERVER_URL='https://github.com', GITHUB_REPOSITORY='fixture/repo', GITHUB_RUN_ID='1')
        with patch.object(ci, 'ROOT', root), patch.dict(os.environ, env):
            ci.export()
        return root / 'artifacts' / 'publish' / ('SOFT-Tracking-' + target + '-' + version + '.zip')

    def test_windows_export_versioned_migrator_and_metadata(self):
        for version in ('3.2.1', '3.3.0'):
            with self.subTest(version=version):
                archive = self.export_fixture('windows-x64-modern', version)
                with zipfile.ZipFile(archive) as bundle:
                    descriptor = json.loads(bundle.read('windows-x64-modern/migration.json'))
                    self.assertEqual(descriptor['file'], 'SOFT-Tracking-Migrate-' + version + '.exe')
                    self.assertEqual(hashlib.sha256(bundle.read('windows-x64-modern/' + descriptor['file'])).hexdigest(), descriptor['sha256'])

    def test_export_rejects_migration_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError, 'Invalid migration'):
            self.export_fixture('windows-x64-modern', '3.3.0', False)

    def test_non_windows_does_not_require_or_export_migrator(self):
        archive = self.export_fixture('linux-x64-modern', '3.3.0')
        with zipfile.ZipFile(archive) as bundle:
            self.assertFalse(any('migration' in name or 'Migrate' in name for name in bundle.namelist()))
