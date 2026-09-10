import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.event_queue import Event, EventQueue


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "outbox.sqlite3"
        self.queue = EventQueue(self.path, reserve_bytes=0)

    def tearDown(self):
        self.queue.close()
        self.temp.cleanup()

    def test_pending_survives_restart_and_read(self):
        event = Event("session", 1700000000, "Tool.exe").to_dict()
        self.queue.push_payload(event)
        self.assertEqual([event], self.queue.batch())
        self.queue.close()
        self.queue = EventQueue(self.path, reserve_bytes=0)
        self.assertEqual([event], self.queue.batch())
        self.assertEqual([event], self.queue.batch())

    def test_partial_ack_and_duplicate_after_lost_ack(self):
        events = [Event("session", 1700000000, "Tool.exe").to_dict() for _ in range(2)]
        for event in events:
            self.queue.push_payload(event)
        self.queue.acknowledge([events[0]["event_id"]])
        self.assertEqual([events[1]], self.queue.batch())
        self.assertTrue(self.queue.push_payload(events[0]))
        self.assertEqual([events[0]["event_id"]], self.queue.confirmed([e["event_id"] for e in events]))

    def test_payload_cannot_change_under_same_id(self):
        event = Event("session", 1700000000, "Tool.exe").to_dict()
        self.queue.push_payload(event)
        changed = dict(event, exe="Other.exe")
        with self.assertRaises(ValueError):
            self.queue.push_payload(changed)
        self.queue.acknowledge([event["event_id"]])
        with self.assertRaises(ValueError):
            self.queue.push_payload(changed)

    def test_rejected_records_stay_on_disk_without_blocking_batch(self):
        event = Event("session", 1700000000, "Tool.exe").to_dict()
        self.queue.push_payload(event)
        self.queue.mark_rejected(event["event_id"], "policy_disabled")
        self.assertEqual([], self.queue.batch())
        self.assertEqual({"pending": 0, "rejected": 1}, self.queue.counts())
        self.queue.retry_rejected()
        self.assertEqual([event], self.queue.batch())

    def test_disk_limit_does_not_delete_old_events(self):
        event = Event("session", 1700000000, "Tool.exe").to_dict()
        self.queue.push_payload(event)
        self.queue.reserve_bytes = 10 ** 30
        with self.assertRaises(OSError):
            self.queue.push(Event("session", 1700000000, "Other.exe"))
        self.assertEqual([event], self.queue.batch())

    def test_multiple_producers_and_batch_limit(self):
        other = EventQueue(self.path, reserve_bytes=0)
        try:
            for index in range(105):
                event = Event("session", 1700000000 + index, "Tool.exe")
                (self.queue if index % 2 else other).push(event)
            self.assertEqual(100, len(self.queue.batch(10000)))
            self.assertEqual(105, self.queue.counts()["pending"])
        finally:
            other.close()

    def test_destructive_drain_forbidden(self):
        with self.assertRaises(RuntimeError):
            self.queue.drain()


if __name__ == "__main__":
    unittest.main()
