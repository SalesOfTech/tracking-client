import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.client import Client
from agent_tracker.core.event_queue import Event
from agent_tracker.core.tracker import ActivityTracker, TrackerConfig
from agent_tracker.runtime import Worker
from test_employee_switch import COMPANY_CODE, OLD_KEY, NEW_KEY, ProfileHttp


class FinishedTracker(ActivityTracker):
    """A real, finished Python thread without native platform polling."""
    def run(self):
        pass


class RuntimeEmployeeSwitchTests(unittest.TestCase):
    def setUp(self):
        no_network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))
        no_network.start()
        self.addCleanup(no_network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.http = ProfileHttp()
        self.client = Client(Path(self.temp.name), self.http)
        self.client.enroll(COMPANY_CODE, OLD_KEY)
        self.old_identity = self.client.state.get('identity')
        self.old_epoch = self.client.employee_epoch
        self.old_queue = self.client.outbox
        self.worker = Worker(self.client)
        self.platform = Mock()
        self.patches = [patch('agent_tracker.runtime.load_platform_adapter', return_value=self.platform),
                        patch('agent_tracker.runtime.ActivityTracker', FinishedTracker),
                        patch('agent_tracker.browser_health.employee_switch_ready', return_value=True, create=True)]
        self.ready = None
        for item in self.patches:
            self.ready = item.start()
            self.addCleanup(item.stop)
        self.worker._start_tracker()
        self.worker.tracker.join(5)

    def tearDown(self):
        tracker = self.worker.tracker
        if isinstance(tracker, ActivityTracker):
            tracker.stop()
            if tracker.ident is not None:
                tracker.join(5)
        self.client.close()
        self.temp.cleanup()

    def switch(self):
        return self.worker.switch_employee(COMPANY_CODE, NEW_KEY)

    def test_switch_closes_old_session_on_old_queue_and_restarts_disabled(self):
        old_tracker = self.worker.tracker
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000000):
            old_tracker._start_session('example.exe', 'Example.exe')
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000030):
            identity = self.switch()
        self.assertEqual(2, identity['user_id'])
        self.assertIsNot(old_tracker, self.worker.tracker)
        self.assertIs(self.client.outbox, self.worker.tracker.events)
        self.assertEqual([], self.worker.tracker.cfg.track_processes)
        self.assertEqual([], self.client.outbox.batch())
        rows = self.old_queue.batch()
        self.assertEqual(1, len(rows))
        self.assertEqual(30, rows[0]['duration_sec'])
        self.assertEqual('employee_switch', rows[0]['reason'])
        self.assertTrue(self.worker.sync_requested.is_set())
        self.assertEqual(0, self.worker.inventory_retry)
        self.platform.get_active_application.assert_not_called()
        self.platform.get_idle_duration_ms.assert_not_called()

    def test_bad_key_or_network_failure_never_stops_existing_tracker(self):
        old_tracker = self.worker.tracker
        for key, error in [('e' * 64, None), (NEW_KEY, ConnectionError('offline'))]:
            self.http.enrollment_error = error
            with self.subTest(key=key), patch.object(old_tracker, 'stop') as stop:
                with self.assertRaises((ValueError, ConnectionError)):
                    self.worker.switch_employee(COMPANY_CODE, key)
                stop.assert_not_called()
            self.assertIs(old_tracker, self.worker.tracker)
            self.assertEqual(self.old_identity, self.client.state.get('identity'))

    def test_initial_browser_barrier_blocks_before_enrollment(self):
        self.ready.return_value = False
        count = len(self.http.calls)
        with patch.object(self.worker.tracker, 'stop') as stop, self.assertRaisesRegex(ValueError, 'not_ready'):
            self.switch()
        stop.assert_not_called()
        self.assertEqual(count, len(self.http.calls))
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_missing_or_non_boolean_browser_capability_fails_closed(self):
        count = len(self.http.calls)
        with patch('agent_tracker.browser_health.employee_switch_ready', None):
            with self.assertRaisesRegex(ValueError, 'not_ready'):
                self.switch()
        self.ready.return_value = {'ready': False}
        with self.assertRaisesRegex(ValueError, 'not_ready'):
            self.switch()
        self.assertEqual(count, len(self.http.calls))
        self.assertEqual(self.old_identity, self.client.state.get('identity'))

    def test_browser_readiness_is_rechecked_and_old_tracker_resumes(self):
        old_tracker = self.worker.tracker
        self.ready.side_effect = [True, False]
        with self.assertRaisesRegex(ValueError, 'not_ready'):
            self.switch()
        self.assertEqual(self.old_epoch, self.client.employee_epoch)
        self.assertIsNot(old_tracker, self.worker.tracker)
        self.assertIs(self.old_queue, self.worker.tracker.events)

    def test_activation_failure_restarts_old_tracker_after_checkpoint(self):
        self.client.refresh_config()
        old_tracker = self.worker.tracker
        old_policy = self.client.state.get('policy')
        with patch.object(self.client, 'activate_employee_switch', side_effect=OSError('storage failure')):
            with self.assertRaises(OSError):
                self.switch()
        self.assertEqual(self.old_identity, self.client.state.get('identity'))
        self.assertEqual(old_policy, self.client.state.get('policy'))
        self.assertIs(self.old_queue, self.worker.tracker.events)
        self.assertIsNot(old_tracker, self.worker.tracker)

    def test_checkpoint_disk_failure_retains_exact_event_and_can_retry(self):
        old_tracker = self.worker.tracker
        pending = Event('session', 1700000000, 'Example.exe', end_timestamp=1700000030, duration_sec=30)
        old_tracker._pending_event = pending
        original_push = self.old_queue.push
        with patch.object(self.old_queue, 'push', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, 'pending_activity'):
                self.switch()
        self.assertEqual(self.old_epoch, self.client.employee_epoch)
        self.assertIs(old_tracker, self.worker.tracker)
        self.assertIs(pending, old_tracker._pending_event)
        self.worker.inventory_retry = time.time() + 3600
        self.worker._cycle(time.time() + 3600, time.time() + 3600, 0)
        self.assertTrue(self.client.state.get('collection_blocked'))
        with patch.object(self.old_queue, 'push', wraps=original_push) as push:
            identity = self.switch()
        push.assert_called_once_with(pending)
        self.assertEqual(2, identity['user_id'])
        self.assertEqual([pending.to_dict()], self.old_queue.batch())
        self.assertIsNone(old_tracker._pending_event)
        self.assertEqual('', self.worker._checkpoint_error)
        self.platform.get_active_application.assert_not_called()

    def test_failed_session_close_freezes_event_then_retry_does_not_extend_it(self):
        old_tracker = self.worker.tracker
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000000):
            old_tracker._start_session('example.exe', 'Example.exe')
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000030):
            with patch.object(self.old_queue, 'push', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(ValueError, 'pending_activity'):
                    self.switch()
        pending = old_tracker._pending_event
        self.assertIsNotNone(pending)
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700100000):
            self.switch()
        self.assertEqual([pending.to_dict()], self.old_queue.batch())
        self.assertEqual(30, pending.duration_sec)
        self.assertEqual(1700000030, pending.end_timestamp)

    def test_unstarted_tracker_after_start_failure_is_not_joined(self):
        with patch.object(FinishedTracker, 'start', side_effect=RuntimeError('thread unavailable')):
            self.worker._start_tracker()
        unstarted = self.worker.tracker
        self.assertIsNone(unstarted.ident)
        with patch.object(unstarted, 'join', side_effect=AssertionError('Must not join unstarted thread')):
            identity = self.switch()
        self.assertEqual(2, identity['user_id'])
        self.assertIsNot(unstarted, self.worker.tracker)

    def test_platform_startup_failure_with_no_tracker_can_switch(self):
        with patch('agent_tracker.runtime.load_platform_adapter', side_effect=RuntimeError('unavailable')):
            self.worker._start_tracker()
        self.assertIsNone(self.worker.tracker)
        identity = self.switch()
        self.assertEqual(2, identity['user_id'])
        self.assertIs(self.client.outbox, self.worker.tracker.events)

    def test_tracker_join_timeout_blocks_without_touching_pending_event(self):
        tracker = Mock()
        tracker.ident = 123
        tracker.is_alive.return_value = True
        tracker._pending_event = object()
        self.worker.tracker = tracker
        with self.assertRaisesRegex(ValueError, 'pending_activity'):
            self.switch()
        tracker.join.assert_called_once_with(timeout=10)
        tracker.events.push.assert_not_called()
        self.assertIs(tracker, self.worker.tracker)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_start_tracker_never_overwrites_pending_activity(self):
        old_tracker = self.worker.tracker
        pending = Event('session', 1700000000, 'Example.exe')
        old_tracker._pending_event = pending
        self.worker._start_tracker()
        self.assertIs(old_tracker, self.worker.tracker)
        self.assertIs(pending, old_tracker._pending_event)

    def test_stop_during_switch_keeps_old_identity_without_restarting(self):
        old_tracker = self.worker.tracker

        def readiness(client):
            self.worker.stopping.set()
            return True

        self.ready.side_effect = readiness
        with self.assertRaisesRegex(ValueError, 'not_ready'):
            self.switch()
        self.assertIs(old_tracker, self.worker.tracker)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_cycle_and_switch_share_the_operation_lock(self):
        entered = threading.Event()
        release = threading.Event()
        attempted = threading.Event()
        finished = threading.Event()
        errors = []
        self.worker.inventory_retry = time.time() + 3600

        def flush():
            entered.set()
            if not release.wait(5):
                raise TimeoutError('test operation barrier')
            return 0

        def cycle():
            try:
                self.worker._cycle(time.time() + 3600, 0, 0)
            except BaseException as error:
                errors.append(error)

        def switch():
            attempted.set()
            try:
                self.switch()
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        cycle_thread = threading.Thread(target=cycle)
        switch_thread = threading.Thread(target=switch)
        with patch.object(self.client, 'flush', side_effect=flush):
            cycle_thread.start()
            try:
                self.assertTrue(entered.wait(5))
                switch_thread.start()
                self.assertTrue(attempted.wait(5))
                self.assertFalse(finished.is_set())
                self.ready.assert_not_called()
                self.assertEqual(self.old_epoch, self.client.employee_epoch)
            finally:
                release.set()
                cycle_thread.join(5)
                if switch_thread.ident is not None:
                    switch_thread.join(5)
        self.assertFalse(cycle_thread.is_alive())
        self.assertFalse(switch_thread.is_alive())
        self.assertEqual([], errors)
        self.assertEqual(2, self.client.state.get('identity')['user_id'])

    def test_graceful_stop_checkpoints_before_setting_stop_and_is_idempotent(self):
        tracker = self.worker.tracker
        pending = Event('session', 1700000000, 'Example.exe', end_timestamp=1700000030, duration_sec=30)
        tracker._pending_event = pending
        original_push = self.old_queue.push

        def save(value):
            self.assertFalse(self.worker.stopping.is_set())
            self.assertIs(pending, value)
            original_push(value)

        with patch.object(self.old_queue, 'push', side_effect=save) as push:
            self.worker.request_graceful_stop()
            self.worker.request_graceful_stop()
        push.assert_called_once_with(pending)
        self.assertTrue(self.worker.stopping.is_set())
        self.assertIs(tracker, self.worker.tracker)
        self.assertIsNone(tracker._pending_event)
        self.assertEqual([pending.to_dict()], self.old_queue.batch())
        self.assertEqual(self.old_identity, self.client.state.get('identity'))

    def test_failed_graceful_stop_retains_live_worker_and_frozen_event_for_retry(self):
        tracker = self.worker.tracker
        pending = Event('session', 1700000000, 'Example.exe', end_timestamp=1700000030, duration_sec=30)
        tracker._pending_event = pending
        self.worker.start()
        try:
            with patch.object(self.old_queue, 'push', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(ValueError, 'pending_activity'):
                    self.worker.request_graceful_stop()
            self.assertTrue(self.worker.is_alive())
            self.assertFalse(self.worker.stopping.is_set())
            self.assertIs(tracker, self.worker.tracker)
            self.assertIs(pending, tracker._pending_event)
            self.assertEqual(self.old_identity, self.client.state.get('identity'))
            self.worker.request_graceful_stop()
            self.worker.join(5)
            self.assertFalse(self.worker.is_alive())
            self.assertEqual([pending.to_dict()], self.old_queue.batch())
            self.assertEqual(1700000030, pending.end_timestamp)
        finally:
            self.worker.request_graceful_stop()
            self.worker.join(5)

    def test_failed_graceful_session_close_retries_without_extending_time(self):
        tracker = self.worker.tracker
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000000):
            tracker._start_session('example.exe', 'Example.exe')
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700000030):
            with patch.object(self.old_queue, 'push', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(ValueError, 'pending_activity'):
                    self.worker.request_graceful_stop()
        pending = tracker._pending_event
        self.assertFalse(self.worker.stopping.is_set())
        self.assertIsNotNone(pending)
        with patch('agent_tracker.core.tracker.current_timestamp', return_value=1700100000):
            self.worker.request_graceful_stop()
        self.assertEqual(30, pending.duration_sec)
        self.assertEqual([pending.to_dict()], self.old_queue.batch())
        self.platform.get_active_application.assert_not_called()

    def test_graceful_stop_join_timeout_does_not_allow_exit(self):
        tracker = Mock()
        tracker.ident = 123
        tracker.is_alive.return_value = True
        tracker._pending_event = object()
        self.worker.tracker = tracker
        with self.assertRaisesRegex(ValueError, 'pending_activity'):
            self.worker.request_graceful_stop()
        self.assertFalse(self.worker.stopping.is_set())
        self.assertIs(tracker, self.worker.tracker)
        tracker.events.push.assert_not_called()

    def test_graceful_stop_handles_unstarted_tracker(self):
        with patch.object(FinishedTracker, 'start', side_effect=RuntimeError('thread unavailable')):
            self.worker._start_tracker()
        tracker = self.worker.tracker
        with patch.object(tracker, 'join', side_effect=AssertionError('Must not join unstarted thread')):
            self.worker.request_graceful_stop()
        self.assertTrue(self.worker.stopping.is_set())

    def test_completed_stop_prevents_late_startup_and_queued_cycle_work(self):
        self.worker.request_graceful_stop()
        with patch('agent_tracker.runtime.ActivityTracker') as create:
            with patch.object(self.client, 'queue_inventory') as inventory:
                with patch.object(self.client, 'refresh_config') as config:
                    with patch.object(self.client, 'flush') as flush:
                        self.worker.run()
                        self.assertEqual((0, 0, 0), self.worker._cycle(0, 0, 0))
        create.assert_not_called()
        inventory.assert_not_called()
        config.assert_not_called()
        flush.assert_not_called()

    def test_graceful_stop_waits_for_operation_lock_before_checkpoint(self):
        attempted = threading.Event()
        errors = []

        def stop():
            attempted.set()
            try:
                self.worker.request_graceful_stop()
            except BaseException as error:
                errors.append(error)

        stopper = threading.Thread(target=stop)
        with patch.object(self.worker, '_checkpoint_tracker', wraps=self.worker._checkpoint_tracker) as checkpoint:
            try:
                with self.worker.operation_lock:
                    stopper.start()
                    self.assertTrue(attempted.wait(5))
                    self.assertFalse(self.worker.stopping.is_set())
                    checkpoint.assert_not_called()
            finally:
                stopper.join(5)
            checkpoint.assert_called_once_with()
        self.assertFalse(stopper.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(self.worker.stopping.is_set())


if __name__ == '__main__':
    unittest.main()
