import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.core import update_control as updates


class UpdateControlTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        atomic_json(self.root / 'current.json', {'version': '3.4.0'})
        updates.publish_status(self.root, 'active', version='3.4.0')

    def test_request_is_local_idempotent_and_acknowledged_by_supervisor(self):
        self.assertTrue(updates.request_check(self.root))
        request = read_json(self.root / updates.REQUEST)
        self.assertEqual(set(request), {'id', 'requested_at'})
        self.assertFalse(updates.request_check(self.root))
        self.assertEqual(request, read_json(self.root / updates.REQUEST))
        self.assertEqual(updates.update_status(self.root)['state'], 'checking')
        self.assertTrue(updates.update_status(self.root)['busy'])
        self.assertEqual(updates.take_request(self.root), request['id'])
        self.assertIsNone(updates.take_request(self.root))
        updates.publish_status(self.root, 'checking', request['id'])
        self.assertTrue(updates.update_status(self.root)['busy'])
        updates.publish_status(self.root, 'active', request['id'], version='3.4.0')
        self.assertFalse(updates.update_status(self.root)['busy'])
        self.assertIsInstance(updates.update_status(self.root)['checked_at'], int)

    def test_download_cannot_be_duplicated_by_manual_check(self):
        updates.publish_status(self.root, 'downloading', version='3.4.1')
        self.assertFalse(updates.request_check(self.root))
        self.assertFalse((self.root / updates.REQUEST).exists())

    def test_unanswered_request_times_out_and_can_be_retried(self):
        with patch.object(updates.time, 'time', return_value=1000):
            updates.request_check(self.root)
        old = read_json(self.root / updates.REQUEST)
        with patch.object(updates.time, 'time', return_value=1121):
            self.assertEqual(updates.update_status(self.root)['state'], 'error')
            self.assertFalse(updates.update_status(self.root)['busy'])
            self.assertTrue(updates.request_check(self.root))
        self.assertNotEqual(old['id'], read_json(self.root / updates.REQUEST)['id'])

    def test_delayed_supervisor_consumes_old_request_once(self):
        atomic_json(self.root / updates.REQUEST, {'id': 'a' * 32, 'requested_at': 1000})
        with patch.object(updates.time, 'time', return_value=2000):
            self.assertEqual(updates.take_request(self.root), 'a' * 32)
            self.assertIsNone(updates.take_request(self.root))

    def test_stuck_check_can_be_requested_again(self):
        with patch.object(updates.time, 'time', return_value=1000):
            updates.publish_status(self.root, 'checking')
        with patch.object(updates.time, 'time', return_value=1121):
            self.assertEqual(updates.update_status(self.root)['state'], 'error')
            self.assertTrue(updates.request_check(self.root))

    def test_missing_installation_or_uninstall_blocks_request(self):
        (self.root / 'current.json').unlink()
        with self.assertRaisesRegex(ValueError, 'update_unavailable'):
            updates.request_check(self.root)
        atomic_json(self.root / 'current.json', {'version': '3.4.0'})
        (self.root / 'uninstall-requested.json').touch()
        with self.assertRaisesRegex(ValueError, 'update_unavailable'):
            updates.request_check(self.root)

    def test_invalid_payload_cannot_supply_a_url_version_or_command(self):
        for value in ([], {'id': 'a' * 32, 'requested_at': 1000, 'url': 'https://invalid.test'},
                      {'id': '../invalid', 'requested_at': 1000},
                      {'id': 'a' * 32, 'requested_at': True}):
            atomic_json(self.root / updates.REQUEST, value)
            self.assertIsNone(updates.take_request(self.root))
        (self.root / updates.REQUEST).write_text('{')
        self.assertIsNone(updates.take_request(self.root))


if __name__ == '__main__':
    unittest.main()
