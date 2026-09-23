"""Collection and delivery do not depend on the desktop UI toolkit."""
import random
import threading
import time

from .core.tracker import ActivityTracker, TrackerConfig
from .platform import load_platform_adapter
from .core.lifecycle import Reporter


class Worker(threading.Thread):
    def __init__(self, client):
        super().__init__(name='TrackingV3', daemon=True)
        self.client = client
        self.stopping = threading.Event()
        self.sync_requested = threading.Event()
        self.tracker = None
        self.capability_error = ''
        self.inventory_retry = 0
        self.operation_lock = threading.RLock()
        self._checkpoint_error = ''
        self.lifecycle = Reporter(client)

    def _start_tracker(self):
        if self.stopping.is_set():
            return
        if self.tracker is not None:
            if self.tracker.is_alive():
                return
            if self.tracker._session is not None or self.tracker._pending_event is not None:
                self._checkpoint_error = 'employee_switch_pending_activity'
                return
        self.tracker = None
        try:
            policy = self.client.policy()
            self.tracker = ActivityTracker(load_platform_adapter(), self.client.outbox,
                TrackerConfig(track_processes=policy.get('track_processes', []) if policy.get('tracking') else []))
            self.tracker.start()
            self.capability_error = ''
        except Exception as error:
            self.capability_error = str(error)

    def _checkpoint_tracker(self):
        tracker = self.tracker
        if tracker is None:
            self._checkpoint_error = ''
            return
        tracker.stop()
        if tracker.ident is not None:
            tracker.join(timeout=10)
        if tracker.is_alive():
            self._checkpoint_error = 'employee_switch_pending_activity'
            raise ValueError(self._checkpoint_error)
        try:
            # A stopped thread may have failed its final durable push. Retry the
            # frozen event, not _poll_once(), which can observe a new application.
            if tracker._pending_event is not None:
                tracker.events.push(tracker._pending_event)
                tracker._pending_event = None
            if tracker._session is not None:
                tracker._close_session(reason='employee_switch')
        except Exception as error:
            self._checkpoint_error = 'employee_switch_pending_activity'
            raise ValueError(self._checkpoint_error) from error
        if tracker._session is not None or tracker._pending_event is not None:
            self._checkpoint_error = 'employee_switch_pending_activity'
            raise ValueError(self._checkpoint_error)
        self._checkpoint_error = ''

    def request_graceful_stop(self):
        """Permit application exit only after the collector has durable custody."""
        with self.operation_lock:
            self._checkpoint_tracker()
            self.stopping.set()
            try:
                self.lifecycle.stopping()
            except Exception:
                # Telemetry failure must not undo a successful activity checkpoint.
                pass

    def switch_employee(self, company_code, employee_key):
        from . import browser_health
        employee_switch_ready = getattr(browser_health, 'employee_switch_ready', None)
        with self.operation_lock:
            if self.stopping.is_set() or not callable(employee_switch_ready) or employee_switch_ready(self.client) is not True:
                raise ValueError('employee_switch_not_ready')
            candidate = self.client.prepare_employee_switch(company_code, employee_key)
            # On failure self.tracker retains its original queue and pending event.
            self._checkpoint_tracker()
            try:
                if self.stopping.is_set() or employee_switch_ready(self.client) is not True:
                    raise ValueError('employee_switch_not_ready')
                identity = self.client.activate_employee_switch(candidate['epoch'],
                    expected_epoch=candidate['expected_epoch'], desktop_sessions_closed=True, browser_epoch_ready=True)
            finally:
                if not self.stopping.is_set():
                    self._start_tracker()
            self.inventory_retry = 0
            self.sync_requested.set()
            return identity

    def run(self):
        with self.operation_lock:
            self._start_tracker()
        next_config = next_flush = 0
        failures = 0
        while not self.stopping.wait(1):
            try:
                with self.operation_lock:
                    if not self.stopping.is_set():
                        self.lifecycle.observe()
                self.lifecycle.deliver()
            except Exception:
                self.client.state.set('lifecycle_error', 'availability_delivery_pending')
            next_config, next_flush, failures = self._cycle(next_config, next_flush, failures)
        try:
            self.lifecycle.deliver(final=True)
        except Exception:
            pass
        with self.operation_lock:
            try:
                self._checkpoint_tracker()
            except ValueError:
                self.client.state.set('collection_blocked', True)

    def _cycle(self, next_config, next_flush, failures):
        with self.operation_lock:
            if self.stopping.is_set():
                return next_config, next_flush, failures
            tracking_error = self._checkpoint_error or (self.tracker.last_error if self.tracker else '')
            self.client.state.set('collection_blocked', bool(tracking_error))
            if self.sync_requested.is_set():
                self.sync_requested.clear()
                next_config = next_flush = 0
            policy = self.client.policy()
            if self.tracker:
                self.tracker.update_config(TrackerConfig(track_processes=policy.get('track_processes', []) if policy.get('tracking') else []))
            if not self.client.state.get('identity'):
                return next_config, next_flush, failures
            try:
                attempted = False
                if time.time() >= next_config:
                    attempted = True
                    policy = self.client.refresh_config()
                    next_config = time.time() + 300
                if time.time() >= self.inventory_retry:
                    self.inventory_retry = time.time() + 300
                    self.client.queue_inventory()
                if time.time() >= next_flush:
                    attempted = True
                    self.client.flush()
                    next_flush = time.time() + int(policy.get('flush_seconds', 30))
                if attempted:
                    failures = 0
                    self.client.state.set('error', tracking_error or self.capability_error)
            except Exception:
                failures = min(failures + 1, 8)
                next_flush = next_config = time.time() + min(300, 2 ** failures + random.random() * 5)
                self.client.state.set('error', 'server_unavailable')
            return next_config, next_flush, failures
