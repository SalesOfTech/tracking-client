import json
from pathlib import Path
import tempfile
import unittest

from agent_tracker.core.client import Client
from test_client import FakeHttp


class DiagnosticsTests(unittest.TestCase):
    def test_offline_diagnostic_survives_restart_and_requires_acknowledgement(self):
        with tempfile.TemporaryDirectory() as folder:
            http = FakeHttp()
            client = Client(Path(folder), http)
            http.device = client.device['device_id']
            client.enroll('a' * 32, 'b' * 64)
            code = client.report_diagnostic('connection', ConnectionError('private'))
            http.fail = True
            client.flush()
            client.close()
            client = Client(Path(folder), http)
            queue = client._queue(client.profiles.active(), diagnostic=True)
            self.assertEqual(queue.batch()[0]['code'], code)
            self.assertEqual(client.queue_counts()['pending'], 0)
            http.fail = False
            self.assertEqual(client.flush(), 0)
            self.assertEqual(queue.batch(), [])
            self.assertFalse(client.state.get('last_web_delivery'))
            client.close()

    def test_durable_bounded_redacted_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(Path(folder))
            code = client.report_diagnostic('connection', ValueError('secret-key https://private/path'))
            self.assertRegex(code, r'^ST-[A-F0-9]{12}$')
            self.assertEqual(code, client.report_diagnostic('connection', RuntimeError('other secret')))
            events = client._queue(client.profiles.active(), diagnostic=True).batch()
            self.assertEqual(client.outbox.batch(), [])
            self.assertEqual(client.queue_counts(), {'pending': 0, 'rejected': 0})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['code'], code)
            self.assertEqual(events[0]['exception'], 'ValueError')
            self.assertNotIn('secret', json.dumps(events))
            self.assertNotIn('private', json.dumps(events))
            self.assertEqual(client.state.get('diagnostic_connection')['code'], code)
            with self.assertRaises(ValueError):
                client.report_diagnostic('secret')
            client.state.close()
            for queue in client._outboxes.values():
                queue.close()


if __name__ == '__main__':
    unittest.main()
