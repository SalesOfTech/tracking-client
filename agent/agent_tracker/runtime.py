"""Collection and delivery do not depend on the desktop UI toolkit."""
import random
import threading
import time

from .core.tracker import ActivityTracker, TrackerConfig
from .platform import load_platform_adapter


class Worker(threading.Thread):
    def __init__(self, client):
        super().__init__(name='TrackingV3', daemon=True)
        self.client = client
        self.stopping = threading.Event()
        self.sync_requested = threading.Event()
        self.tracker = None
        self.capability_error = ''
        self.inventory_retry = 0

    def run(self):
        try:
            self.tracker = ActivityTracker(load_platform_adapter(), self.client.outbox, TrackerConfig())
            self.tracker.start()
        except Exception as error:
            self.capability_error = str(error)
        next_config = next_flush = 0
        failures = 0
        while not self.stopping.wait(1):
            tracking_error = self.tracker.last_error if self.tracker else ''
            self.client.state.set('collection_blocked', bool(tracking_error))
            if self.sync_requested.is_set():
                self.sync_requested.clear()
                next_config = next_flush = 0
            policy = self.client.policy()
            if self.tracker:
                self.tracker.update_config(TrackerConfig(track_processes=policy.get('track_processes', []) if policy.get('tracking') else []))
            if not self.client.state.get('identity'):
                continue
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
        if self.tracker:
            self.tracker.stop()
            self.tracker.join(timeout=10)
