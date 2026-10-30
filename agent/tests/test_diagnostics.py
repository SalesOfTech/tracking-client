import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_tracker.core.client import Client
from test_client import FakeHttp


class DiagnosticsTests(unittest.TestCase):
    def test_update_report_survives_restart_and_uploads_when_activity_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            http = FakeHttp()
            client = Client(Path(folder), http)
            http.device = client.device['device_id']
            client.enroll('a' * 32, 'b' * 64)
            details = dict(update_stage='health', target_version='3.5.1', attempt=1, retry_at=1800000000)
            code = client.report_diagnostic('update', ValueError('secret-key private-path'), update=details)
            self.assertEqual(code, client.report_diagnostic('update', update=details))
            client.close()
            client = Client(Path(folder), http)
            self.addCleanup(client.close)
            event = client._queue(client.profiles.active(), diagnostic=True).batch()[0]
            self.assertEqual('health', event['update_stage'])
            self.assertNotIn('secret', json.dumps(event))
            client.outbox.push_payload(dict(event_id='a' * 32, type='session', timestamp=1700000000))
            post = http.post_json
            def send(path, payload, **kwargs):
                if payload['events'][0]['type'] != 'diagnostic':
                    raise ConnectionError('activity failure')
                return post(path, payload, **kwargs)
            with patch.object(http, 'post_json', side_effect=send):
                with self.assertRaises(ConnectionError):
                    client.flush()
            self.assertEqual([], client._queue(client.profiles.active(), diagnostic=True).batch())
            self.assertEqual(1, client.queue_counts()['pending'])
            client.close()

    def test_update_report_allowlist_and_short_deduplication_window(self):
        with tempfile.TemporaryDirectory() as folder:
            client = Client(Path(folder))
            self.addCleanup(client.close)
            details = dict(update_stage='download', target_version='3.5.1', attempt=0, retry_at=1800000000)
            with patch('time.time', return_value=1700000000):
                first = client.report_diagnostic('update', update=details)
                self.assertEqual(first, client.report_diagnostic('update', update=details))
            with patch('time.time', return_value=1700000300):
                self.assertNotEqual(first, client.report_diagnostic('update', update=details))
            for fields in ({'update_stage': 'secret'}, {'target_version': 'https://secret'},
                           {'attempt': True}, {'retry_at': -1}, {'message': 'private'}):
                with self.assertRaises(ValueError):
                    client.report_diagnostic('update', update=dict(details, **fields))
            client.close()

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
