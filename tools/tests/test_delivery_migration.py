import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from tools.delivery import export


class MigrationDeliveryTests(unittest.TestCase):
    def archive(self, root, migration=True, tamper=False):
        target, version = 'windows-x64-modern', '3.3.0'
        path = root / ('SOFT-Tracking-' + target + '-' + version + '.zip')
        with zipfile.ZipFile(path, 'w') as z:
            setup = {'target': target, 'version': version, 'file': 'SOFT-Tracking-Setup-3.3.0.exe',
                     'sha256': hashlib.sha256(b'setup').hexdigest()}
            manifest = {'target': target, 'version': version, 'size': 7,
                        'sha256': hashlib.sha256(b'release').hexdigest()}
            z.writestr(target + '/setup.json', json.dumps(setup))
            z.writestr(target + '/manifest.json', json.dumps({'payload': base64.b64encode(json.dumps(manifest).encode()).decode()}))
            z.writestr(target + '/' + setup['file'], b'setup')
            z.writestr(target + '/release-3.3.0.zip', b'release')
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


if __name__ == '__main__':
    unittest.main()
