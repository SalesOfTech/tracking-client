import json
from pathlib import Path
import tempfile
import unittest

from agent_tracker.core.client import Client


class DiagnosticsTests(unittest.TestCase):
    def test_durable_bounded_redacted_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(Path(folder))
            code = client.report_diagnostic('connection', ValueError('secret-key https://private/path'))
            self.assertRegex(code, r'^ST-[A-F0-9]{12}$')
            self.assertEqual(code, client.report_diagnostic('connection', RuntimeError('other secret')))
            events = client.outbox.batch()
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
