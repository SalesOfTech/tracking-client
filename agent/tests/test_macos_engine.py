import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_tracker.setup_payload import setup_payload


class MacOSEngineTests(unittest.TestCase):
    def test_dispatch_removes_internal_arguments_and_preserves_wrapper_identity(self):
        entry = runpy.run_path(str(Path(__file__).parents[1]/'macos_engine_entry.py'))
        names = {'app': ('runtime_entry', 'soft-tracking-app'),
                 'native': ('native_entry', 'soft-tracking-host-app'),
                 'bootstrap': ('bootstrap_entry', 'soft-tracking'),
                 'bootstrap-native': ('bootstrap_native_entry', 'soft-tracking-host'),
                 'setup': ('setup_entry', 'SOFT-Tracking-Setup')}
        with tempfile.TemporaryDirectory() as temporary:
            for role, (module, name) in names.items():
                wrapper = Path(temporary)/name
                wrapper.touch()
                argv = ['engine', '--tracking-role', role, '--tracking-wrapper', str(wrapper), '--', 'caller']
                def dispatched(actual, run_name):
                    self.assertEqual((actual, run_name), (module, '__main__'))
                    self.assertEqual(sys.argv, [str(wrapper), 'caller'])
                    self.assertEqual(sys.executable, str(wrapper))
                    self.assertEqual(sys._MEIPASS, '/sealed-engine')
                with patch.object(sys, 'argv', argv), patch.object(sys, 'executable', 'engine'), \
                        patch.object(sys, '_MEIPASS', '/sealed-engine', create=True), \
                        patch('runpy.run_module', side_effect=dispatched):
                    entry['main']()

    def test_native_payload_is_bound_to_the_installer_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            contents = Path(temporary)/'Setup.app/Contents'
            payload = contents/'Resources/setup-payload'
            payload.mkdir(parents=True)
            with patch.object(sys, 'platform', 'darwin'), \
                    patch.object(sys, 'executable', str(contents/'MacOS/SOFT-Tracking-Setup')), \
                    patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD=str(payload)):
                self.assertEqual(setup_payload(), payload.resolve())
                with patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD=temporary):
                    with self.assertRaises(ValueError):
                        setup_payload()

    def test_other_platforms_ignore_native_override(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, '_MEIPASS', '/frozen', create=True), \
                patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD='/untrusted'):
            self.assertEqual(setup_payload(), Path('/frozen/setup-payload'))

    @unittest.skipIf(os.name == 'nt', 'POSIX parent path aliases')
    def test_native_payload_accepts_parent_alias_but_not_different_bundle_or_leaf_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            contents = root / 'real/Setup.app/Contents'
            payload = contents / 'Resources/setup-payload'
            payload.mkdir(parents=True)
            alias = root / 'alias'
            alias.symlink_to(root / 'real', target_is_directory=True)
            external = alias / 'Setup.app/Contents/Resources/setup-payload'
            with patch.object(sys, 'platform', 'darwin'), \
                    patch.object(sys, 'executable', str(contents / 'MacOS/SOFT-Tracking-Setup')), \
                    patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD=str(external)):
                self.assertEqual(setup_payload(), payload)
                other = root / 'Other.app/Contents/Resources/setup-payload'
                other.mkdir(parents=True)
                with patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD=str(other)):
                    with self.assertRaises(ValueError):
                        setup_payload()
                leaf = root / 'linked-payload'
                leaf.symlink_to(payload, target_is_directory=True)
                with patch.dict(os.environ, SOFT_TRACKING_SETUP_PAYLOAD=str(leaf)):
                    with self.assertRaises(ValueError):
                        setup_payload()
