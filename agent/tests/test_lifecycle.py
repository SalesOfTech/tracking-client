import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tracker.core.client import Client
from agent_tracker.core.lifecycle import record, flush, Reporter


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = Client(Path(self.temp.name))
        self.client.state.set('identity', {'company_id': 36, 'user_id': 1})
        self.client.profiles.update_active(self.client.employee_epoch,
                                          identity={'company_id': 36, 'user_id': 1})

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def rows(self):
        return self.client.state.db.execute('SELECT id,epoch FROM lifecycle_outbox').fetchall()

    def test_acknowledgement_removes_only_confirmed_events(self):
        first = record(self.client, 'started', 100)
        second = record(self.client, 'heartbeat', 160)
        with patch.object(self.client, '_post', return_value={'ok': True, 'accepted': [first]}):
            flush(self.client)
        self.assertEqual([(second, self.client.employee_epoch)], self.rows())

    def test_invalid_response_or_network_failure_keeps_evidence(self):
        event = record(self.client, 'stop_requested')
        for response in [None, {}, {'ok': True, 'accepted': ['unknown']},
                         {'ok': True, 'accepted': [event, event]}]:
            with patch.object(self.client, '_post', return_value=response):
                with self.assertRaises(ValueError):
                    flush(self.client)
            self.assertEqual(1, len(self.rows()))
        with patch.object(self.client, '_post', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                flush(self.client)
        self.assertEqual(1, len(self.rows()))

    def test_no_record_before_enrollment(self):
        self.client.profiles.update_active(self.client.employee_epoch, identity=None)
        self.assertIsNone(record(self.client, 'heartbeat'))

    def test_events_survive_restart(self):
        event = record(self.client, 'uninstall_requested')
        self.client.close()
        self.client = Client(Path(self.temp.name))
        self.assertEqual(event, self.rows()[0][0])

    def test_reporter_does_not_backfill_suspended_time(self):
        reporter = Reporter(self.client)
        with patch('agent_tracker.core.lifecycle.time.monotonic', side_effect=[0, 1, 3600]), \
                patch('agent_tracker.core.lifecycle.flush'):
            reporter.observe()
            reporter.observe()
            reporter.observe()
        self.assertEqual(2, len(self.rows()))

    def test_no_heartbeat_after_explicit_stop(self):
        reporter = Reporter(self.client)
        reporter.observe()
        reporter.stopping()
        reporter.next_record = 0
        reporter.observe()
        reporter.stopping()
        self.assertEqual(2, len(self.rows()))

    def test_final_delivery_prioritizes_recent_stop(self):
        for _ in range(101):
            record(self.client, 'heartbeat')
        event = record(self.client, 'stop_requested')
        with patch.object(self.client, '_post', return_value={'ok': True, 'accepted': [event]}) as post:
            flush(self.client, active_only=True)
        self.assertEqual(event, post.call_args.args[2]['events'][0]['id'])
        self.assertEqual(101, len(self.rows()))


if __name__ == '__main__':
    unittest.main()
