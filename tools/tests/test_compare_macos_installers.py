import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_macos_installers import detach_image, extract_installer, payload_metadata, TARGET


class PublishedInstallerTests(unittest.TestCase):
    def test_detach_retries_only_its_own_mount_then_forces(self):
        with mock.patch('compare_macos_installers.command', side_effect=[{'code': 16}] * 3 + [{'code': 0}]) as command, \
                mock.patch('compare_macos_installers.time.sleep'):
            self.assertEqual(detach_image('/private/test/mount'), {'code': 0})
        self.assertEqual(command.call_args_list, [mock.call('hdiutil', 'detach', '/private/test/mount')] * 3 +
                         [mock.call('hdiutil', 'detach', '-force', '/private/test/mount')])

    def test_payload_reports_flattened_framework_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as archive:
                archive.writestr('app/soft-tracking-app', b'bootloader')
                archive.writestr('app/_internal/Python.framework/Versions/Current/Python', b'not a symlink')
            with zipfile.ZipFile(root / 'outer.zip', 'w') as archive:
                archive.writestr(TARGET + '/manifest.json', '{"manifest":{"version":"3.4.1"}}')
                archive.writestr(TARGET + '/release-3.4.1.zip', inner.getvalue())
            result = payload_metadata(root / 'outer.zip', '3.4.1', root)
            self.assertEqual(result['runtime_bytes'], 10)
            self.assertFalse(next(iter(result['framework_entries'].values()))['symlink'])

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
