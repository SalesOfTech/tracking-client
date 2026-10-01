from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_tracker import admin_control
from agent_tracker.core.event_queue import EventQueue


class AdminControlTests(unittest.TestCase):
    def test_status_does_not_require_or_probe_administrator_authorization(self):
        control = admin_control.AdminControl(Path('install'))
        with patch.object(admin_control, 'autostart_status', return_value={'registered': True}), \
                patch.object(subprocess, 'run') as run, patch.object(subprocess, 'Popen') as spawn:
            result = control.status()
        self.assertEqual(result['scope'], 'per_user')
        self.assertFalse(result['admin_required'])
        self.assertFalse(result['force_kill_protected'])
        self.assertFalse(result['request_pending'])
        self.assertEqual(result['authorization'], {'available': True, 'mechanism': 'current_user'})
        self.assertNotIn('authorized', result)
        run.assert_not_called()
        spawn.assert_not_called()

    def test_uninstalled_status_does_not_invent_startup_registration(self):
        result = admin_control.AdminControl().status()
        self.assertFalse(result['autostart']['registered'])
        self.assertEqual(result['autostart']['error'], 'not_installed')

    def test_stop_always_accepts_current_user_without_system_helpers(self):
        control = admin_control.AdminControl()
        with patch.object(subprocess, 'run') as run, patch.object(subprocess, 'Popen') as spawn:
            for _ in range(2):
                self.assertEqual(control.request_stop(), {'authorized': True, 'state': 'authorized'})
                self.assertFalse(control._request_lock.locked())
        run.assert_not_called()
        spawn.assert_not_called()

    def test_duplicate_stop_request_does_not_reenter(self):
        control = admin_control.AdminControl()
        with control._request_lock:
            self.assertEqual(control.request_stop(), {'authorized': False, 'state': 'busy'})
        self.assertEqual(control.request_stop(), {'authorized': True, 'state': 'authorized'})

    def test_stop_control_preserves_durable_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            event = {'event_id': 'a' * 32, 'type': 'navigation', 'timestamp': 1700000000}
            queue = EventQueue(root / 'outbox.sqlite3')
            queue.push_payload(event)
            queue.close()
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            self.assertTrue(admin_control.AdminControl(root / 'install').request_stop()['authorized'])
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})
            queue = EventQueue(root / 'outbox.sqlite3')
            try:
                self.assertEqual(queue.batch(), [event])
            finally:
                queue.close()


if __name__ == '__main__':
    unittest.main()
