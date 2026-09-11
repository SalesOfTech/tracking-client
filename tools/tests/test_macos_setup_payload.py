"""Inspect opaque POSIX installer payloads without compiling native binaries."""
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools import release


class MacOSSetupPayloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.setup = self.root / 'setup-payload'
        self.binary = self.setup / 'electron/SoftTrackingUI.app/Contents/MacOS/SoftTrackingUI'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'\xcf\xfa\xed\xfeopaque-electron-fixture')
        self.binary.chmod(0o755)
        (self.setup / 'release.zip').write_bytes(b'signed-payload-fixture')

    def test_electron_added_after_analysis_without_binary_processing(self):
        before = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        mode = self.binary.stat().st_mode
        analysis = Mock(return_value=SimpleNamespace(
            pure=[], scripts=[], binaries=[('Python', '/trusted/Python', 'BINARY')], datas=[]))
        executable, bundle = Mock(), Mock()
        source = release.setup_payload_spec('setup_entry.py', 'SOFT-Tracking-Setup',
                                            [(self.setup, 'setup-payload')], ['PySide6', 'tkinter'], macos=True)
        exec(compile(source, '<macos-setup-spec>', 'exec'),
             {'Analysis': analysis, 'PYZ': Mock(), 'EXE': executable, 'BUNDLE': bundle})
        self.assertEqual(analysis.call_args.kwargs['datas'], [])
        self.assertEqual(analysis.call_args.kwargs['binaries'], [])
        self.assertEqual(analysis.call_args.kwargs['hiddenimports'], ['_cffi_backend'])
        self.assertEqual(analysis.call_args.kwargs['excludes'], ['PySide6', 'tkinter'])
        collected = executable.call_args.args[3]
        self.assertEqual({row[0] for row in collected}, {
            'setup-payload/electron/SoftTrackingUI.app/Contents/MacOS/SoftTrackingUI',
            'setup-payload/release.zip'})
        self.assertTrue(all(row[2] == 'DATA' for row in collected))
        self.assertEqual(executable.call_args.args[2], [('Python', '/trusted/Python', 'BINARY')])
        self.assertFalse(executable.call_args.kwargs['console'])
        self.assertFalse(executable.call_args.kwargs['upx'])
        self.assertEqual(bundle.call_args.kwargs['name'], 'SOFT-Tracking-Setup.app')
        self.assertEqual(hashlib.sha256(self.binary.read_bytes()).hexdigest(), before)
        self.assertEqual(self.binary.stat().st_mode, mode)

    def test_only_posix_electron_installer_uses_opaque_spec(self):
        for system, entry, ui, expected in [
            ('Darwin', 'setup_entry.py', 'electron', True),
            ('Darwin', 'setup_entry.py', 'qt', False),
            ('Darwin', 'runtime_entry.py', 'electron', False),
            ('Windows', 'setup_entry.py', 'electron', False),
            ('Linux', 'setup_entry.py', 'electron', True),
            ('Linux', 'setup_entry.py', 'qt', False),
            ('Linux', 'runtime_entry.py', 'electron', False),
        ]:
            with self.subTest(system=system, entry=entry, ui=ui), \
                    patch.object(release.platform, 'system', return_value=system), \
                    patch.object(release.subprocess, 'run') as run:
                release.freeze(entry, 'fixture', self.root / 'dist', self.root / 'work',
                               [(self.setup, 'setup-payload')], ui=ui)
            args = run.call_args.args[0]
            self.assertEqual(args[-1].endswith('.spec'), expected)
            self.assertTrue(run.call_args.kwargs['check'])
            if expected:
                source = Path(args[-1]).read_text(encoding='utf-8')
                self.assertIn("'PySide2'", source)
                self.assertIn("'_tkinter'", source)
                self.assertNotIn('--add-data', args)

    def test_linux_keeps_onefile_console_executable_without_app_bundle(self):
        analysis = Mock(return_value=SimpleNamespace(pure=[], scripts=[], binaries=[], datas=[]))
        executable, bundle = Mock(), Mock()
        source = release.setup_payload_spec('setup_entry.py', 'SOFT-Tracking-Setup',
                                            [(self.setup, 'setup-payload')], [], macos=False)
        exec(compile(source, '<linux-setup-spec>', 'exec'),
             {'Analysis': analysis, 'PYZ': Mock(), 'EXE': executable, 'BUNDLE': bundle})
        self.assertTrue(executable.call_args.kwargs['console'])
        self.assertNotIn('exclude_binaries', executable.call_args.kwargs)
        bundle.assert_not_called()

    def test_pyinstaller_preserves_executable_data_without_rewriting_it(self):
        from PyInstaller.building import api
        package = api.PKG.__new__(api.PKG)
        package.name = str(self.root / 'fixture.pkg')
        package.toc = [('electron', str(self.binary), 'DATA'),
                       ('release.zip', str(self.setup / 'release.zip'), 'DATA')]
        package.exclude_binaries = False
        package.cdict = {'DATA': True}
        package.python_lib_name = 'fixture-python'
        with patch.object(api, 'CArchiveWriter') as writer, \
                patch.object(api, 'process_collected_binary') as rewrite, \
                patch.object(api.os, 'access', side_effect=lambda path, mode: Path(path) == self.binary):
            package.assemble()
        rewrite.assert_not_called()
        entries = {entry[0]: entry for entry in writer.call_args.args[1]}
        self.assertEqual(entries['electron'], ('electron', str(self.binary), True, 'b'))
        self.assertEqual(entries['release.zip'][-1], 'x')

    def test_duplicate_payload_destinations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            release.setup_payload_spec('setup_entry.py', 'fixture',
                                       [(self.setup, 'setup-payload')] * 2, [], macos=True)

    def test_payload_symlinks_remain_rejected(self):
        with patch.object(Path, 'is_symlink', lambda path: path == self.binary):
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                release.setup_payload_spec('setup_entry.py', 'fixture',
                                           [(self.setup, 'setup-payload')], [], macos=True)


if __name__ == '__main__':
    unittest.main()
