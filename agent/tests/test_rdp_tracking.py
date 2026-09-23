import ctypes
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.tracker import ActivityTracker, TrackerConfig
from agent_tracker.platform.base import ActiveApplication
from agent_tracker.platform.windows import WindowsPlatform


class RdpTrackerTests(unittest.TestCase):
    def setUp(self):
        self.platform = Mock()
        self.platform.is_session_active.return_value = True
        self.platform.get_idle_duration_ms.return_value = 0
        self.platform.get_active_application.return_value = ActiveApplication(1, 'LBIS4G.Win.exe')
        self.queue = Mock()
        self.tracker = ActivityTracker(self.platform, self.queue, TrackerConfig(track_processes=['lbis4g.win.exe']))

    def poll(self, seconds):
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1000 + seconds), \
                patch('agent_tracker.core.tracker.time.monotonic', return_value=seconds):
            self.tracker._poll_once()

    def test_disconnect_closes_and_reconnect_starts_fresh(self):
        self.poll(0)
        self.poll(1)
        self.platform.is_session_active.return_value = False
        self.poll(2)
        self.poll(62)
        self.assertEqual(self.queue.push.call_count, 1)
        event = self.queue.push.call_args.args[0]
        self.assertEqual((event.duration_sec, event.reason), (1, 'session_inactive'))
        self.platform.is_session_active.return_value = True
        self.poll(63)
        self.assertEqual(self.tracker._session.started_at, 1063)

    def test_idle_stops_after_configured_grace(self):
        for second in range(31):
            self.platform.get_idle_duration_ms.return_value = second * 1000
            self.poll(second)
        self.assertIsNone(self.tracker._session)
        self.assertEqual(self.queue.push.call_args.args[0].duration_sec, 30)

    def test_poll_suspension_does_not_count_missing_hours(self):
        self.poll(0)
        self.poll(1)
        self.poll(7200)
        self.assertEqual(self.queue.push.call_args.args[0].duration_sec, 1)
        self.assertEqual(self.tracker._session.started_at, 8200)

    def test_platform_failure_does_not_mean_active(self):
        self.poll(0)
        self.poll(1)
        self.platform.get_idle_duration_ms.side_effect = OSError('unavailable')
        self.poll(2)
        self.assertIsNone(self.tracker._session)
        self.assertTrue(self.tracker.last_error)
        self.assertEqual(self.queue.push.call_args.args[0].duration_sec, 1)

    def test_switching_to_untracked_program_ends_session(self):
        self.poll(0)
        self.platform.get_active_application.return_value = ActiveApplication(2, 'other.exe')
        self.poll(1)
        self.assertIsNone(self.tracker._session)


@unittest.skipUnless(os.name == 'nt', 'Windows API types')
class WindowsSessionTests(unittest.TestCase):
    def test_idle_after_long_uptime_and_wrap(self):
        adapter = WindowsPlatform()
        self.assertIs(adapter._kernel32.GetTickCount.restype, ctypes.wintypes.DWORD)
        for tick, last, expected in [(25 * 86400000, 25 * 86400000 - 600000, 600000),
                                     (5000, 0xffffffff - 4999, 10000)]:
            def read(pointer):
                pointer._obj.dwTime = last
                return 1
            adapter._user32 = Mock()
            adapter._user32.GetLastInputInfo.side_effect = read
            adapter._kernel32 = Mock()
            adapter._kernel32.GetTickCount.return_value = tick
            self.assertEqual(adapter.get_idle_duration_ms(), expected)
        adapter._user32.GetLastInputInfo.side_effect = None
        adapter._user32.GetLastInputInfo.return_value = 0
        with self.assertRaises(OSError):
            adapter.get_idle_duration_ms()

    def adapter(self, state):
        adapter = WindowsPlatform()
        adapter._session_id = Mock(return_value=7)
        adapter._wts = Mock()
        value = ctypes.c_int(state)
        def query(server, session, kind, buffer, size):
            self.assertEqual((session, kind), (7, 8))
            buffer._obj.value = ctypes.addressof(value)
            size._obj.value = ctypes.sizeof(value)
            return 1
        adapter._wts.WTSQuerySessionInformationW.side_effect = query
        adapter._user32 = Mock()
        adapter._desktop_name = Mock(side_effect=['Default', 'Default'])
        return adapter

    def test_only_own_active_session_and_unlocked_desktop(self):
        adapter = self.adapter(0)
        self.assertTrue(adapter.is_session_active())
        adapter._wts.WTSFreeMemory.assert_called_once()
        adapter._user32.CloseDesktop.assert_called_once()
        adapter = self.adapter(4)
        self.assertFalse(adapter.is_session_active())
        adapter._user32.OpenInputDesktop.assert_not_called()
        adapter = self.adapter(0)
        adapter._desktop_name.side_effect = ['Winlogon', 'Default']
        self.assertFalse(adapter.is_session_active())

    def test_query_failure_is_not_activity(self):
        adapter = self.adapter(0)
        adapter._wts.WTSQuerySessionInformationW.side_effect = None
        adapter._wts.WTSQuerySessionInformationW.return_value = 0
        with self.assertRaises(OSError):
            adapter.is_session_active()


if __name__ == '__main__':
    unittest.main()
