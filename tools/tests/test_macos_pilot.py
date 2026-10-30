import argparse
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.macos_pilot import engine_spec
from tools.release import build_plan


class MacOSPilotTests(unittest.TestCase):
    def test_stable_arm_uses_proven_engine_without_changing_other_targets(self):
        with patch.dict(os.environ, SOFT_TRACKING_MACOS_PILOT='', SOFT_TRACKING_UI='electron'), \
                patch('tools.release.require_github_runner'):
            for version in ('3.5.2', '3.6.0'):
                args = argparse.Namespace(version=version, profile='modern')
                for target in [('macos', 'arm64'), ('macos', 'x64'), ('windows', 'arm64'), ('linux', 'arm64')]:
                    with self.subTest(version=version, target=target), \
                            patch('agent_tracker.core.target.runtime_target', return_value=target):
                        metadata = build_plan(args)['metadata']
                        self.assertNotIn('build_channel', metadata)
                        self.assertEqual(metadata.get('macos_engine'),
                                         'sealed-onedir-v1' if target[0] == 'macos' else None)

    def test_engine_architecture_is_native_not_relabelled(self):
        self.assertIn("target_arch='x86_64'", engine_spec('x64'))
        self.assertIn("target_arch='arm64'", engine_spec('arm64'))
        with self.assertRaises(ValueError):
            engine_spec('x86')

    def test_engine_uses_collect_bundle_not_onefile(self):
        spec = engine_spec()
        compile(spec, '<engine spec>', 'exec')
        self.assertIn('exclude_binaries=True', spec)
        self.assertIn('COLLECT(exe, a.binaries, a.datas', spec)
        self.assertIn('BUNDLE(coll', spec)
        for module in ['runtime_entry', 'native_entry', 'bootstrap_entry', 'bootstrap_native_entry', 'setup_entry']:
            self.assertIn(repr(module), spec)

    def test_pilot_rejects_other_platforms_and_stable_versions(self):
        args = argparse.Namespace(version='3.5.2-rc.1', profile='modern')
        with patch.dict(os.environ, SOFT_TRACKING_MACOS_PILOT='1', SOFT_TRACKING_UI='electron'), \
                patch('tools.release.require_github_runner'):
            for target in [('windows', 'x64'), ('linux', 'arm64'), ('macos', 'x64')]:
                with patch('agent_tracker.core.target.runtime_target', return_value=target):
                    with self.assertRaises(ValueError):
                        build_plan(args)
            with patch('agent_tracker.core.target.runtime_target', return_value=('macos', 'arm64')):
                self.assertEqual(build_plan(args)['metadata']['macos_engine'], 'sealed-onedir-v1')
                args.version = '3.5.2'
                with self.assertRaises(ValueError):
                    build_plan(args)
