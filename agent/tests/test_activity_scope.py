import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.activity_scope import automatic_application
from agent_tracker.core.tracker import ActivityTracker, TrackerConfig
from agent_tracker.platform.base import ActiveApplication


class ActivityScopeTests(unittest.TestCase):
    def test_extended_collection_requires_both_explicit_flags(self):
        for policy in ({}, {'extended_activity': True}, {'tracking': True},
                       {'tracking': False, 'extended_activity': True},
                       {'tracking': True, 'extended_activity': 'true'}):
            self.assertFalse(TrackerConfig.from_policy(policy).extended_activity)
        self.assertTrue(TrackerConfig.from_policy({'tracking': True, 'extended_activity': True}).extended_activity)

    def test_unlisted_app_keeps_identifier_without_user_path_or_title(self):
        active = ActiveApplication(12, 'Tool.exe', title='Confidential document',
                                   executable=r'C:\Users\Alice\Tools\Tool.exe')
        self.assertEqual(automatic_application(active), ('tool.exe', 'Tool.exe'))
        queue = Mock()
        platform = Mock()
        platform.is_session_active.return_value = True
        platform.get_idle_duration_ms.return_value = 0
        platform.get_active_application.return_value = active
        tracker = ActivityTracker(platform, queue, TrackerConfig(extended_activity=True))
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000000):
            tracker._poll_once()
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000010):
            tracker._close_session('test')
        payload = queue.push.call_args.args[0].to_dict()
        self.assertEqual(payload['application_id'], 'tool.exe')
        self.assertEqual(payload['duration_sec'], 10)
        self.assertNotIn('Alice', str(payload))
        self.assertNotIn('Confidential', str(payload))

    def test_mac_bundle_id_preserves_application_breakdown(self):
        self.assertEqual(automatic_application(ActiveApplication(2, 'Excel',
            bundle_id='com.microsoft.Excel', executable='/Applications/Excel.app/Contents/MacOS/Excel')),
            ('com.microsoft.excel', 'Excel'))

    def test_default_scope_still_requires_allowlist(self):
        active = ActiveApplication(1, 'tool.exe')
        tracker = ActivityTracker(Mock(), Mock(), TrackerConfig(track_processes=['excel.exe']))
        self.assertIsNone(tracker._match_tracked_application(active))
        self.assertEqual(tracker._match_tracked_application(ActiveApplication(1, 'EXCEL.EXE')),
                         ('excel.exe', 'EXCEL.EXE'))

    def test_stock_shells_browsers_and_own_processes_not_autocollected(self):
        for name in ('explorer.exe', 'LockApp.exe', 'chrome.exe', 'msedge.exe',
                     'firefox.exe', 'Safari', 'Finder', 'Dock', 'soft-tracking-app',
                     'SOFT Tracking', 'CalculatorApp.exe', 'Notepad.exe'):
            with self.subTest(name=name):
                self.assertIsNone(automatic_application(ActiveApplication(1, name)))
        self.assertIsNone(automatic_application(ActiveApplication(1, 'Something',
            executable=r'C:\Windows\System32\Something.exe')))
        self.assertIsNone(automatic_application(ActiveApplication(1, 'Preview',
            bundle_id='com.apple.Preview', executable='/Applications/Preview.app/Contents/MacOS/Preview')))

    def test_explicit_working_list_overrides_automatic_exclusions(self):
        tracker = ActivityTracker(Mock(), Mock(), TrackerConfig(track_processes=['notepad.exe'], extended_activity=True))
        self.assertEqual(tracker._match_tracked_application(ActiveApplication(1, 'notepad.exe')),
                         ('notepad.exe', 'notepad.exe'))

    def test_server_exclusions_never_enter_outbox(self):
        for name in ('WindowsTerminal.exe', 'pwsh.exe', 'bash', 'fish',
                     'google-chrome-stable', 'chromium-browser', 'firefox-esr',
                     'microsoft-edge-stable', 'brave-browser', 'kscreenlocker_greet'):
            with self.subTest(name=name):
                tracker = ActivityTracker(Mock(), Mock(), TrackerConfig(extended_activity=True))
                self.assertIsNone(tracker._match_tracked_application(ActiveApplication(
                    1, name, executable='/Applications/' + name)))
        self.assertIsNone(automatic_application(ActiveApplication(
            1, 'Session', bundle_id='org.gnome.shell')))

    def test_disabled_or_locked_session_does_not_observe_app(self):
        platform = Mock()
        platform.is_session_active.return_value = False
        tracker = ActivityTracker(platform, Mock(), TrackerConfig(extended_activity=True))
        tracker._poll_once()
        platform.get_active_application.assert_not_called()

    def test_policy_disable_closes_current_session(self):
        queue = Mock()
        tracker = ActivityTracker(Mock(), queue, TrackerConfig(extended_activity=True))
        tracker._start_session('tool.exe', 'Tool.exe')
        tracker.update_config(TrackerConfig.from_policy({'tracking': True}))
        tracker._poll_once()
        self.assertIsNone(tracker._session)
        self.assertEqual(queue.push.call_args.args[0].reason, 'disabled')

    def test_malformed_application_identifiers_not_collected(self):
        for name in ('', 'x' * 161, 'bad\nname', '/private/name', 'private\\name',
                     ' leading', 'trailing ', 'a..b', 'v3-other-test', 'tool --option',
                     'emoji-\U0001f600', '\u010d' * 81, "tool's name"):
            with self.subTest(name=name):
                self.assertIsNone(automatic_application(ActiveApplication(1, name)))


if __name__ == '__main__':
    unittest.main()
