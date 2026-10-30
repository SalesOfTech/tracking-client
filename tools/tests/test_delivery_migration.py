import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.delivery import export


class MigrationDeliveryTests(unittest.TestCase):
    def archive(self, root, migration=True, tamper=False, target='windows-x64-modern',
                version='3.3.0', engine=None, installer=None, tamper_setup=False):
        path = root / ('SOFT-Tracking-' + target + '-' + version + '.zip')
        with zipfile.ZipFile(path, 'w') as z:
            extension = {'windows': 'exe', 'macos': 'dmg', 'linux': 'run'}[target.split('-')[0]]
            setup = {'target': target, 'version': version,
                     'file': installer or 'SOFT-Tracking-Setup-' + version + '.' + extension,
                     'sha256': hashlib.sha256(b'setup').hexdigest()}
            manifest = {'target': target, 'version': version, 'os': target.split('-')[0], 'size': 7,
                        'sha256': hashlib.sha256(b'release').hexdigest()}
            if engine is not None:
                manifest['macos_engine'] = engine
                setup['macos_engine'] = engine
            z.writestr(target + '/setup.json', json.dumps(setup))
            z.writestr(target + '/manifest.json', json.dumps({'payload': base64.b64encode(json.dumps(manifest).encode()).decode()}))
            z.writestr(target + '/' + setup['file'], b'wrong' if tamper_setup else b'setup')
            z.writestr(target + '/release-' + version + '.zip', b'release')
            if migration:
                z.writestr(target + '/migration.json', json.dumps({
                    'target': target, 'version': version, 'file': 'SOFT-Tracking-Migrate-3.3.0.exe',
                    'sha256': hashlib.sha256(b'migration').hexdigest()}))
                z.writestr(target + '/SOFT-Tracking-Migrate-3.3.0.exe', b'wrong' if tamper else b'migration')
        return path

    def test_migration_export_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = export(self.archive(root), root, '3.3.0')
            self.assertEqual(3, len(paths))
            self.assertEqual(b'migration', paths[2].read_bytes())

    def test_old_release_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(2, len(export(self.archive(root, migration=False), root, '3.3.0')))

    def test_bad_migration_hash_fails_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                export(self.archive(root, tamper=True), root, '3.3.0')

    def test_sealed_macos_pkg_export_is_byte_identical(self):
        for target in ('macos-arm64-modern', 'macos-x64-modern'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                filename = 'SOFT-Tracking-Setup-3.5.2.pkg'
                archive = self.archive(root, migration=False, target=target, version='3.5.2',
                                       engine='sealed-onedir-v1', installer=filename)
                paths = export(archive, root, '3.5.2')
                self.assertEqual([p.name for p in paths],
                                 [target + '--' + filename, target + '--release-3.5.2.zip'])
                self.assertEqual([p.read_bytes() for p in paths], [b'setup', b'release'])

    def test_existing_installer_formats_remain_supported(self):
        for target in ('macos-x64-modern', 'macos-arm64-modern', 'windows-x64-modern',
                       'windows-x86-legacy', 'windows-x64-legacy', 'windows-arm64-modern',
                       'linux-x64-modern', 'linux-arm64-modern'):
            for version in ('3.5.1', '3.5.2'):
                with self.subTest(target=target, version=version), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    paths = export(self.archive(root, migration=False, target=target, version=version), root, version)
                    self.assertEqual([p.read_bytes() for p in paths], [b'setup', b'release'])

    def test_pkg_requires_exact_version_platform_and_sealed_engine(self):
        cases = [
            ('macos-arm64-modern', '3.5.1', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.1.pkg'),
            ('macos-x64-modern', '3.5.2', None, 'SOFT-Tracking-Setup-3.5.2.pkg'),
            ('macos-arm64-modern', '3.5.2', 'unknown', 'SOFT-Tracking-Setup-3.5.2.pkg'),
            ('windows-x64-modern', '3.5.2', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.2.pkg'),
            ('linux-arm64-modern', '3.5.2', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.2.pkg'),
            ('macos-arm64-modern', '3.5.2', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.1.pkg'),
            ('macos-x64-modern', '3.5.2', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.2.dmg'),
            ('macos-x64-modern', '3.5.2', 'sealed-onedir-v1', 'SOFT-Tracking-Setup-3.5.2.zip'),
            ('macos-arm64-modern', '3.5.2', 'sealed-onedir-v1', '../SOFT-Tracking-Setup-3.5.2.pkg'),
        ]
        for target, version, engine, filename in cases:
            with self.subTest(case=(target, version, engine, filename)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = self.archive(root, migration=False, target=target, version=version,
                                       engine=engine, installer=filename)
                with self.assertRaisesRegex(ValueError, 'Invalid installer'):
                    export(archive, root, version)
                self.assertEqual(list(root.glob('*--*')), [])

    def test_pkg_hash_mismatch_fails_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root, migration=False, target='macos-arm64-modern', version='3.5.2',
                                   engine='sealed-onedir-v1', installer='SOFT-Tracking-Setup-3.5.2.pkg',
                                   tamper_setup=True)
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                export(archive, root, '3.5.2')


if __name__ == '__main__':
    unittest.main()
