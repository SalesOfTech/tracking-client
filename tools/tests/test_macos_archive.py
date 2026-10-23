"""Check the real PyInstaller archive format, without executing native binaries."""
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.macos_archive import verify_frozen, verify_python_archive


class MacOSArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'fixture'
        self.source.write_bytes(b'framework-fixture')
        self.metadata = self.root / 'Info.plist'
        self.metadata.write_bytes(plistlib.dumps({'CFBundleExecutable': 'Python', 'CFBundlePackageType': 'FMWK'}))
        prefix = 'Python.framework/'
        self.entries = [
            (prefix + 'Versions/3.10/Python', str(self.source), True, 'b'),
            (prefix + 'Versions/3.10/Resources/Info.plist', str(self.metadata), True, 'x'),
            (prefix + 'Python', 'Versions/Current/Python', False, 'n'),
            (prefix + 'Resources', 'Versions/Current/Resources', False, 'n'),
            (prefix + 'Versions/Current', '3.10', False, 'n'),
            ('Python', prefix + 'Versions/3.10/Python', False, 'n'),
        ]

    def archive(self, entries=None):
        from PyInstaller.archive.writers import CArchiveWriter
        path = self.root / 'soft-tracking-app'
        CArchiveWriter(str(path), self.entries if entries is None else entries,
                       'Python')
        return path

    def test_versioned_framework_with_aliases_passes(self):
        verify_python_archive(self.archive())

    def test_flattened_aliases_are_rejected(self):
        for index in (2, 3, 4):
            with self.subTest(index=index):
                entries = self.entries.copy()
                entries[index] = (entries[index][0], str(self.source), True, 'b')
                with self.assertRaisesRegex(ValueError, 'Python.framework'):
                    verify_python_archive(self.archive(entries))

    def test_missing_library_metadata_or_framework_is_rejected(self):
        for entries in (self.entries[1:], self.entries[:1] + self.entries[2:], []):
            with self.subTest(entries=len(entries)), self.assertRaises(ValueError):
                verify_python_archive(self.archive(entries))

    def test_unexpected_copies_and_external_version_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'flattened'):
            verify_python_archive(self.archive(self.entries + [
                ('Python.framework/Resources/Info.plist', str(self.source), True, 'x')]))
        with self.assertRaisesRegex(ValueError, 'version symlink'):
            verify_python_archive(self.archive(self.entries[:4] + self.entries[5:] + [
                ('Python.framework/Versions/Current', '../../outside', False, 'n')]))

    def test_bootloader_alias_must_point_to_the_embedded_version(self):
        for replacement in ([], [('Python', str(self.source), True, 'b')],
                            [('Python', 'Python.framework/Versions/3.9/Python', False, 'n')],
                            [('Python', '/Library/Frameworks/Python.framework/Python', False, 'n')]):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, 'bootloader alias'):
                verify_python_archive(self.archive(self.entries[:-1] + replacement))

    def test_wrong_framework_alias_targets_are_rejected(self):
        for index, target in ((2, '/Library/Frameworks/Python.framework/Python'),
                              (3, 'Versions/3.9/Resources'), (4, '3.9')):
            entries = self.entries.copy()
            entries[index] = (entries[index][0], target, False, 'n')
            with self.subTest(index=index), self.assertRaises(ValueError):
                verify_python_archive(self.archive(entries))

    def test_invalid_framework_metadata_is_rejected(self):
        for data in (b'not a plist', b'<?xml version="1.0"?><plist><dict>',
                     plistlib.dumps([]), plistlib.dumps({'CFBundleExecutable': 'Other'})):
            self.metadata.write_bytes(data)
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, 'metadata'):
                verify_python_archive(self.archive())

    def test_framework_alias_collisions_are_rejected(self):
        for name in ('Python.framework', 'Python/child', 'Python.framework/Versions/Current/Python',
                     'Python.framework/Versions/3.10'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                verify_python_archive(self.archive(self.entries + [(name, str(self.source), True, 'x')]))

    def test_case_of_versioned_path_is_significant(self):
        entries = self.entries.copy()
        entries[0] = ('Python.framework/versions/3.10/Python', str(self.source), True, 'b')
        with self.assertRaisesRegex(ValueError, 'Missing embedded Python library'):
            verify_python_archive(self.archive(entries))

    def test_noncanonical_versioned_paths_are_rejected(self):
        from PyInstaller.archive.readers import CArchiveReader
        executable = self.archive()
        for suffix in ('../outside', './extra', 'Resources//extra', 'Resources\\extra'):
            archive = CArchiveReader(str(executable))
            # The writer normalizes paths; emulate a malformed TOC from another producer.
            archive.toc['Python.framework/Versions/3.10/' + suffix] = (0, 0, 0, 0, 'x')
            with self.subTest(suffix=suffix), \
                    patch('PyInstaller.archive.readers.CArchiveReader', return_value=archive), \
                    self.assertRaisesRegex(ValueError, 'Unsafe Python.framework archive path'):
                verify_python_archive(executable)

    def test_regular_file_zip_staging_preserves_archive_and_launcher_path(self):
        from tools import release
        from agent_tracker import bootstrap
        from agent_tracker.core.signed_updates import stage_archive
        executable = self.archive()
        executable.chmod(0o755)
        payload = self.root / 'payload'
        release.copy_runtime(self.root, payload / 'app', executable.name, 'macos')
        package = self.root / 'release.zip'
        release.write_payload_archive(payload, package)
        installation = self.root / 'install'
        installed = installation / 'versions/9.8.7'
        stage_archive(package, {'size': package.stat().st_size, 'sha256': release.file_digest(package)}, installed)
        release.atomic_json(installation / 'current.json', {'version': '9.8.7'})
        with patch.object(bootstrap, 'os', SimpleNamespace(name='posix')):
            launched = bootstrap.app_path(installation)
        self.assertEqual(launched, installed / 'app/soft-tracking-app')
        self.assertEqual(launched.read_bytes(), executable.read_bytes())
        self.assertTrue(launched.stat().st_mode & 0o111)
        self.assertFalse(any(path.is_symlink() for path in installed.rglob('*')))
        verify_python_archive(launched)

    def test_native_signature_verification_is_strict_and_does_not_resign(self):
        executable = self.archive()
        bundle = self.root / 'Setup.app'
        with patch('tools.macos_archive.subprocess.run') as run:
            verify_frozen(executable, bundle)
        self.assertEqual(run.call_args_list[0].args[0],
                         ['codesign', '--verify', '--strict', str(executable)])
        self.assertEqual(run.call_args_list[1].args[0],
                         ['codesign', '--verify', '--deep', '--strict', str(bundle)])
        self.assertTrue(all(call.kwargs['check'] for call in run.call_args_list))

    def test_signature_failure_stops_packaging(self):
        with patch('tools.macos_archive.subprocess.run',
                   side_effect=subprocess.CalledProcessError(1, ['codesign'])):
            with self.assertRaises(subprocess.CalledProcessError):
                verify_frozen(self.archive())

    def test_invalid_framework_fails_before_signature_check(self):
        with patch('tools.macos_archive.subprocess.run') as run:
            with self.assertRaises(ValueError):
                verify_frozen(self.archive([]))
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
