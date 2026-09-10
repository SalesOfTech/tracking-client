from __future__ import annotations

import logging
import threading
import time
from typing import Dict

from .event_queue import EventQueue, current_timestamp
from .networking import HttpClient
from .storage import StorageManager


LOG = logging.getLogger(__name__)


class EventDispatcher(threading.Thread):
    daemon = True

    def __init__(
        self,
        queue: EventQueue,
        http: HttpClient,
        storage: StorageManager,
        flush_seconds: int,
        endpoint: str = "/soft-agent/events",
        envelope: Dict[str, str] | None = None,
    ) -> None:
        super().__init__(name="EventDispatcher")
        self.queue = queue
        self.http = http
        self.storage = storage
        self.flush_seconds = flush_seconds
        self.endpoint = endpoint
        self.envelope = envelope or {}
        self._stop_event = threading.Event()
        self._retry_delay_until = 0.0

    def update_interval(self, flush_seconds: int) -> None:
        self.flush_seconds = flush_seconds

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            wait_for = self._compute_wait()
            if self._stop_event.wait(wait_for):
                break
            self._flush()
        self._flush()

    def _compute_wait(self) -> float:
        now = time.time()
        if now < self._retry_delay_until:
            return max(0.0, self._retry_delay_until - now)
        return self.flush_seconds

    def _flush(self) -> None:
        events = self.queue.batch()
        if not events:
            return
        payload = dict(self.envelope)
        payload["events"] = events
        payload["timestamp"] = current_timestamp()
        try:
            response = self.http.post_json(self.endpoint, payload)
            acknowledged, rejected = validate_ack(response, self.envelope.get("install_id"), events)
            self.queue.acknowledge(acknowledged)
            for event_id, code in rejected.items():
                self.queue.mark_rejected(event_id, code)
            self._retry_delay_until = 0.0
            LOG.info("Database confirmed %s events; %s rejected", len(acknowledged), len(rejected))
        except Exception as exc:
            LOG.error("Failed to dispatch events: %s", exc)
            self._retry_delay_until = time.time() + 60


def validate_ack(response: dict, device_id: str, events: list) -> tuple:
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise ValueError("Missing database acknowledgement")
    ack = response.get("ack")
    if not isinstance(ack, dict) or ack.get("protocol") != 3 or ack.get("device_id") != device_id:
        raise ValueError("Wrong acknowledgement protocol/device")
    ids = ack.get("event_ids")
    rejected = response.get("rejected", {})
    sent = {event["event_id"] for event in events}
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
        raise ValueError("Malformed acknowledgement IDs")
    if len(ids) != len(set(ids)) or not set(ids).issubset(sent):
        raise ValueError("Acknowledgement contains unsent/duplicate IDs")
    if not isinstance(rejected, dict) or not set(rejected).issubset(sent) or set(ids).intersection(rejected):
        raise ValueError("Malformed rejection list")
    if any(not isinstance(code, str) or len(code) > 80 for code in rejected.values()):
        raise ValueError("Invalid rejection code")
    return ids, rejected
