from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'agent'))
import release
import smoke_package


class UninstallPackagingTests(unittest.TestCase):
    def test_current_mac_version_runs_runtime_and_real_onefile_harness(self):
        env = {'FIXTURE': 'isolated'}
        with patch.object(smoke_package, 'run_frozen', return_value=subprocess.CompletedProcess([], 0, b'', b'')) as run, \
                patch.object(release, 'freeze') as freeze:
            smoke_package.smoke_macos_uninstall(Path('/fixture/app'), {'os': 'macos'}, '3.4.2', env)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], [str(Path('/fixture/app')), '--uninstall-self-test'])
        self.assertEqual(run.call_args_list[0].kwargs['env'], env)
        self.assertEqual(freeze.call_args.args[:2], ('uninstall_smoke_entry.py', 'SoftTrackingUninstallSmoke'))
        self.assertEqual(freeze.call_args.kwargs, {'console': True, 'ui': 'electron'})
        self.assertEqual(run.call_args_list[1].kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertEqual(run.call_args_list[1].args[0][1:], ['--uninstall-self-test'])

    def test_old_and_non_mac_packages_do_not_get_unsupported_smoke_flags(self):
        with patch.object(smoke_package, 'run_frozen') as run, patch.object(release, 'freeze') as freeze:
            for system, version in (('macos', '3.4.1'), ('windows', '3.4.2'), ('linux', '3.4.2')):
                smoke_package.smoke_macos_uninstall(Path('/fixture/app'), {'os': system}, version, {})
            run.assert_not_called()
            freeze.assert_not_called()

    def test_onefile_failure_fails_packaging(self):
        with patch.object(smoke_package, 'run_frozen', side_effect=[subprocess.CompletedProcess([], 0, b'', b''),
                    subprocess.CompletedProcess([], 1, b'', b'bootloader fixture')]), patch.object(release, 'freeze'):
            with self.assertRaisesRegex(RuntimeError, 'Real onefile macOS uninstall smoke failed'):
                smoke_package.smoke_macos_uninstall(Path('/fixture/app'), {'os': 'macos'}, '3.4.2', {})


if __name__ == '__main__':
    unittest.main()
