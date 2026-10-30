import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tracker.core.client import Client
from agent_tracker.core.files import atomic_json
from agent_tracker.core import update_diagnostics
from test_client import FakeHttp


class UpdateDiagnosticTests(unittest.TestCase):
    def test_supervisor_report_is_durable_and_flushed_without_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'install'
            root.mkdir()
            http = FakeHttp()
            client = Client(root.parent, http)
            http.device = client.device['device_id']
            client.enroll('a' * 32, 'b' * 64)
            secret = client.device['device_secret']
            client.close()
            atomic_json(root / 'failed.json', dict(version='3.5.1', attempts=2, retry_at=1800000000))
            code = update_diagnostics.report_failure(root, 'health_exit', '3.5.1', RuntimeError(secret))
            self.assertRegex(code, r'^ST-[A-F0-9]{12}$')
            http.fail = True
            with patch.object(update_diagnostics, 'HttpClient', return_value=http):
                update_diagnostics.flush_reports(root)
                http.fail = False
                update_diagnostics.flush_reports(root)
            event = http.calls[-1][1]['events'][0]
            self.assertEqual('health_exit', event['update_stage'])
            self.assertEqual(2, event['attempt'])
            self.assertEqual('3.5.1', event['target_version'])
            self.assertNotIn(secret, json.dumps(event))
            client = Client(root.parent, http)
            self.assertEqual([], client._queue(client.profiles.active(), diagnostic=True).batch())
            client.close()

    def test_diagnostic_storage_failure_never_prevents_rollback(self):
        with patch.object(update_diagnostics, 'Client', side_effect=OSError('disk full')):
            self.assertIsNone(update_diagnostics.report_failure('/fixture/install', 'health'))
            update_diagnostics.flush_reports('/fixture/install')
