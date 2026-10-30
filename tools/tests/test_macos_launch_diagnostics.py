"""A native binary smoke check must not mask a failed LaunchServices start."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macos_launch_diagnostics import assert_launch_results


class MacLaunchResultsTests(unittest.TestCase):
    def healthy(self):
        return [dict(launch_method=method, exit_code=0, health=dict(ready=True, ui='electron'))
                for method in ('launchservices', 'direct')]

    def test_both_paths_require_renderer_readiness(self):
        assert_launch_results(self.healthy())
        for change in (dict(timeout=True), dict(exit_code=1), dict(health=None),
                       dict(health={'ready': False, 'ui': 'electron'}),
                       dict(health={'ready': True, 'ui': 'qt'})):
            with self.subTest(change=change):
                results = self.healthy()
                results[0].update(change)
                with self.assertRaisesRegex(AssertionError, 'launchservices'):
                    assert_launch_results(results)

    def test_direct_success_cannot_replace_missing_launchservices(self):
        for results in ([], self.healthy()[1:], self.healthy() + self.healthy()):
            with self.subTest(results=results), self.assertRaises(AssertionError):
                assert_launch_results(results)
