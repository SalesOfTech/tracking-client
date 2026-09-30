import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tracker import automatic_migration, migration_smoke
from agent_tracker.core.files import atomic_json


class MigrationSmokeTests(unittest.TestCase):
    def test_launcher_stub_does_not_intercept_platform_subprocesses(self):
        def install(bundle, root, code, **options):
            # Windows platform detection can invoke a subprocess during install.
            result = subprocess.run([sys.executable, '-c', 'print("platform-check")'],
                                    check=True, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.stdout.strip(), 'platform-check')
            atomic_json(root / 'current.json', {'version': '3.4.0'})
            launcher = root / 'launcher'
            launcher.touch()
            return launcher

        with patch.object(automatic_migration.installer, 'install', side_effect=install) as install_mock:
            migration_smoke.run_automatic(Path('isolated-fixture'))
        install_mock.assert_called_once()


if __name__ == '__main__':
    unittest.main()
