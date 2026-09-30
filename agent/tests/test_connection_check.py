import unittest
from unittest.mock import Mock
from agent_tracker.runtime import Worker


class ConnectionCheckTests(unittest.TestCase):
    def test_success_waits_for_config_and_flush(self):
        client = Mock()
        worker = Worker(client)
        self.assertEqual(worker.check_connection(), 'check_server_ok')
        client.refresh_config.assert_called_once()
        client.flush.assert_called_once()

    def test_network_failure_is_not_reported_as_success(self):
        client = Mock()
        client.refresh_config.side_effect = TimeoutError()
        self.assertEqual(Worker(client).check_connection(), 'check_offline')
        client.flush.assert_not_called()

    def test_stopping_does_not_report_success(self):
        worker = Worker(Mock())
        worker.stopping.set()
        self.assertEqual(worker.check_connection(), 'check_pending')
        worker.client.refresh_config.assert_not_called()
