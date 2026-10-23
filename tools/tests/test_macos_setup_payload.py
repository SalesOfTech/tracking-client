"""Inspect opaque POSIX installer payloads without compiling native binaries."""
import hashlib
import json
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

    def test_macos_runtime_is_onefile_but_other_platforms_stay_onedir(self):
        for system in ('Darwin', 'Windows', 'Linux'):
            with self.subTest(system=system), \
                    patch.object(release.platform, 'system', return_value=system), \
                    patch.object(release.subprocess, 'run') as run:
                release.freeze('runtime_entry.py', 'soft-tracking-app',
                               self.root / 'dist', self.root / 'work', ui='electron')
                command = run.call_args.args[0]
                self.assertEqual('--onefile' in command, system == 'Darwin')
                self.assertEqual('--onedir' in command, system != 'Darwin')

    def test_macos_runtime_copy_preserves_launcher_path_and_executable(self):
        source, destination = self.root / 'dist', self.root / 'app'
        source.mkdir()
        destination.mkdir()
        executable = source / 'soft-tracking-app'
        executable.write_bytes(b'onefile-with-embedded-framework')
        executable.chmod(0o755)
        release.copy_runtime(source, destination, executable.name, 'macos')
        installed = destination / executable.name
        self.assertEqual(installed.read_bytes(), executable.read_bytes())
        self.assertEqual(installed.stat().st_mode, executable.stat().st_mode)
        self.assertEqual(list(destination.iterdir()), [installed])

    def test_macos_runtime_copy_rejects_onedir_instead_of_flattening_framework(self):
        source, destination = self.root / 'dist', self.root / 'app'
        (source / 'soft-tracking-app').mkdir(parents=True)
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, 'onefile'):
            release.copy_runtime(source, destination, 'soft-tracking-app', 'macos')
        self.assertEqual(list(destination.iterdir()), [])

    def test_macos_runtime_copy_rejects_symlinks_and_non_executable_files(self):
        source, destination = self.root / 'dist', self.root / 'app'
        source.mkdir()
        executable = source / 'soft-tracking-app'
        executable.write_bytes(b'not-executable')
        executable.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'onefile'):
            release.copy_runtime(source, destination, executable.name, 'macos')
        executable.chmod(0o755)
        with patch.object(Path, 'is_symlink', return_value=True), self.assertRaisesRegex(ValueError, 'onefile'):
            release.copy_runtime(source, destination, executable.name, 'macos')
        self.assertFalse(destination.exists())

    def test_mac_package_smoke_enables_strict_onefile_extraction_only_on_mac(self):
        # Stop at the first frozen check; installation and native execution are mocked.
        with patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
            from tools import smoke_package
        for system in ('macos', 'windows', 'linux'):
            captured = {}

            def capture(app, metadata, version, env):
                captured.update(env)
                raise RuntimeError('stop before native execution')

            with self.subTest(system=system), \
                    patch.object(smoke_package, 'install'), \
                    patch.object(smoke_package, 'assert_ui_payload'), \
                    patch.object(smoke_package.Path, 'is_file', lambda path: path.name != 'migration.json'), \
                    patch.object(smoke_package.Path, 'read_text', side_effect=[json.dumps({'os': system}),
                                                                             '{"version":"9.8.7"}']), \
                    patch.object(smoke_package.os, 'environ', {}), \
                    patch.object(smoke_package, 'smoke_macos_uninstall', side_effect=capture), \
                    self.assertRaisesRegex(RuntimeError, 'stop before native execution'):
                smoke_package.main(self.setup)
            self.assertEqual(captured.get('PYINSTALLER_STRICT_UNPACK_MODE'), '1' if system == 'macos' else None)

    def test_other_platforms_keep_runtime_directory_contents(self):
        for os_name in ('windows', 'linux'):
            with self.subTest(os_name=os_name):
                source = self.root / os_name / 'dist'
                destination = self.root / os_name / 'app'
                internal = source / 'runtime/_internal'
                internal.mkdir(parents=True)
                (source / 'runtime/runtime').write_bytes(b'executable')
                (internal / 'library').write_bytes(b'library')
                release.copy_runtime(source, destination, 'runtime', os_name)
                self.assertEqual((destination / 'runtime').read_bytes(), b'executable')
                self.assertEqual((destination / '_internal/library').read_bytes(), b'library')

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
