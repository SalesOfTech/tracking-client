import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_macos_installers import extract_installer, TARGET


class PublishedInstallerTests(unittest.TestCase):
    def fixture(self, folder, name, digest=None):
        bundle = folder / 'release.zip'
        data = b'exact disk image bytes'
        metadata = {'file': name, 'sha256': digest or hashlib.sha256(data).hexdigest()}
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr(TARGET + '/setup.json', json.dumps(metadata))
            archive.writestr(TARGET + '/' + name, data)
        return bundle, data, metadata

    def test_extracts_only_verified_published_installer(self):
        for version in ('3.4.1', '3.5.0', '3.5.1'):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle, data, metadata = self.fixture(root, 'SOFT-Tracking-Setup-' + version + '.dmg')
                self.assertEqual(extract_installer(bundle, version, root / 'out.dmg'), metadata)
                self.assertEqual((root / 'out.dmg').read_bytes(), data)

    def test_rejects_wrong_file_or_corrupt_image(self):
        for name, digest in (('../other.dmg', None), ('SOFT-Tracking-Setup-3.5.1.dmg', None),
                             ('SOFT-Tracking-Setup-3.4.1.dmg', '0' * 64)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle, _, _ = self.fixture(root, name, digest)
                with self.assertRaises(ValueError):
                    extract_installer(bundle, '3.4.1', root / 'out.dmg')
