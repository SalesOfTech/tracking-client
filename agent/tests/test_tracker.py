import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.tracker import ActivityTracker,TrackerConfig


class TrackerTests(unittest.TestCase):
    def test_disabled_tracker_does_not_read_foreground_application(self):
        platform=Mock()
        tracker=ActivityTracker(platform,Mock(),TrackerConfig())
        tracker._poll_once()
        platform.get_active_application.assert_not_called()
        platform.get_idle_duration_ms.assert_not_called()

    def test_disk_failure_preserves_frozen_event_without_counting_retry_time(self):
        queue=Mock()
        queue.push.side_effect=[OSError('full'),None]
        tracker=ActivityTracker(Mock(),queue,TrackerConfig())
        with patch('agent_tracker.core.tracker.current_timestamp',return_value=1700000000):
            tracker._start_session('tool.exe','Tool.exe')
        with patch('agent_tracker.core.tracker.current_timestamp',return_value=1700000030):
            with self.assertRaises(OSError): tracker._close_session('test')
        pending=tracker._pending_event
        with patch('agent_tracker.core.tracker.current_timestamp',return_value=1700100000):
            tracker._poll_once()
        self.assertIs(queue.push.call_args_list[1].args[0],pending)
        self.assertEqual(1700000030,pending.end_timestamp)
        self.assertEqual(30,pending.duration_sec)
        self.assertIsNone(tracker._pending_event)


if __name__=='__main__': unittest.main()
