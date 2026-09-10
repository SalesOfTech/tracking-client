import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.dispatcher import validate_ack


class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.events = [{"event_id": "a" * 32}, {"event_id": "b" * 32}]
        self.device = "c" * 32

    def reply(self, ids):
        return {"ok": True, "ack": {"protocol": 3, "device_id": self.device, "event_ids": ids}}

    def test_partial_ack(self):
        self.assertEqual((["a" * 32], {}), validate_ack(self.reply(["a" * 32]), self.device, self.events))

    def test_legacy_success_is_not_delivery(self):
        for reply in ({}, {"ok": True}, {"ok": True, "stored": 0}):
            with self.assertRaises(ValueError):
                validate_ack(reply, self.device, self.events)

    def test_wrong_device_and_unsent_ids(self):
        with self.assertRaises(ValueError):
            validate_ack(self.reply(["a" * 32]), "d" * 32, self.events)
        for ids in (["e" * 32], ["a" * 32, "a" * 32], [42]):
            with self.assertRaises(ValueError):
                validate_ack(self.reply(ids), self.device, self.events)

    def test_invalid_rejections_cannot_remove_data(self):
        response = self.reply(["a" * 32])
        response["rejected"] = {"a" * 32: "bad"}
        with self.assertRaises(ValueError):
            validate_ack(response, self.device, self.events)


if __name__ == "__main__":
    unittest.main()
