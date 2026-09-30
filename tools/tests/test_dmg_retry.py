import subprocess
import unittest
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release


class DmgRetryTests(unittest.TestCase):
    def test_busy_is_retried_but_success_requires_zero_exit(self):
        with patch.object(release.subprocess, 'run', side_effect=[
            subprocess.CompletedProcess([], 1, '', 'hdiutil: create failed - Resource busy'),
            subprocess.CompletedProcess([], 0, 'created', '')]) as run, patch.object(release.time, 'sleep') as sleep:
            release.create_macos_image('setup.app', 'setup.dmg')
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(10)

    def test_other_failures_and_exhausted_retries_still_fail(self):
        for message, count in [('Permission denied', 1), ('Resource busy', 3)]:
            with patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', message)) as run, patch.object(release.time, 'sleep'):
                with self.assertRaises(subprocess.CalledProcessError):
                    release.create_macos_image('setup.app', 'setup.dmg')
                self.assertEqual(run.call_count, count)


if __name__ == '__main__':
    unittest.main()
