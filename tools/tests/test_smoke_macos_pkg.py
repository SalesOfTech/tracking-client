from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smoke_macos_pkg import processes_with_argument


class PackageProcessTests(unittest.TestCase):
    def test_skips_protected_and_exited_processes_without_accepting_other_runner(self):
        protected, exited, other, own = [Mock() for _ in range(4)]
        protected.cmdline.side_effect = psutil.AccessDenied(1)
        exited.cmdline.side_effect = psutil.NoSuchProcess(2)
        other.cmdline.return_value = ['/bin/bash', '/another/runner.sh']
        own.cmdline.return_value = ['/bin/bash', '/private/test/runner.sh', 'label']
        with patch('smoke_macos_pkg.psutil.process_iter', return_value=[protected, exited, other, own]):
            self.assertEqual(processes_with_argument('/private/test/runner.sh'), [own])
