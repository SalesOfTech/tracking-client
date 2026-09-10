import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_tracker.core.client import Client
from agent_tracker.electron_desktop import Controller, serve, validate


class ElectronControllerTests(unittest.TestCase):
    def test_closing_notification_releases_inherited_pipe(self):
        controller = Controller(health=True)
        child = Mock()
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO(b'{"id":1,"action":"ready","input":{}}\n{"event":"closing"}\n')
        child.returncode = 0
        child.poll.side_effect = lambda: 0 if child.stdin.closed else None
        with tempfile.TemporaryDirectory() as directory, \
                patch('agent_tracker.electron_desktop.restore_links'), \
                patch('agent_tracker.electron_desktop.electron_path', return_value=Mock(is_file=lambda: True)), \
                patch('agent_tracker.electron_desktop.subprocess.Popen', return_value=child):
            self.assertEqual(serve(controller, Path(directory)), 0)
        self.assertTrue(controller.ready)
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)
        child.terminate.assert_not_called()

    def test_health_deadline_applies_even_after_ready(self):
        controller = Controller(health=True)
        controller.ready = True
        child = Mock()
        child.stdin, child.stdout = io.BytesIO(), io.BytesIO()
        child.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory, \
                patch('agent_tracker.electron_desktop.restore_links'), \
                patch('agent_tracker.electron_desktop.electron_path', return_value=Mock(is_file=lambda: True)), \
                patch('agent_tracker.electron_desktop.subprocess.Popen', return_value=child), \
                patch('agent_tracker.electron_desktop.time.monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, 'health check timed out'):
                serve(controller, Path(directory))
        child.terminate.assert_called_once()
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)

    def test_shutdown_has_deadline_and_kills_a_child_ignoring_terminate(self):
        controller = Controller()
        controller.ready = True
        child = Mock()
        child.stdin = io.BytesIO()
        child.stdout = io.BytesIO(b'{"event":"closing"}\n')
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('electron', 10), 0]
        with tempfile.TemporaryDirectory() as directory, \
                patch('agent_tracker.electron_desktop.restore_links'), \
                patch('agent_tracker.electron_desktop.electron_path', return_value=Mock(is_file=lambda: True)), \
                patch('agent_tracker.electron_desktop.subprocess.Popen', return_value=child), \
                patch('agent_tracker.electron_desktop.time.monotonic', side_effect=[0, 0, 11]):
            with self.assertRaisesRegex(RuntimeError, 'shutdown timed out'):
                serve(controller, Path(directory))
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)

    def test_invalid_bundle_aliases_stop_before_starting_electron_for_both_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'electron'
            for controller in (Controller(), Controller(bundle=Path(directory), health=True)):
                with patch('agent_tracker.electron_desktop.restore_links', side_effect=ValueError('Invalid map')) as restore, \
                        patch('agent_tracker.electron_desktop.subprocess.Popen') as spawn:
                    with self.assertRaisesRegex(ValueError, 'Invalid map'):
                        serve(controller, folder)
                    restore.assert_called_once_with(folder)
                    spawn.assert_not_called()

    def test_rejects_commands_outside_the_ui_contract(self):
        for action, data in [('exec', {}), ('open', {'target': 'https://invalid.test'}),
                             ('preferences', {'theme': 'invalid'}), ('status', {'extra': True}),
                             ('resume', {'paused': False}), ('resume', {'policy': {}}), ('pause', {}),
                             ('enroll', {'code': 'a'*32, 'key': 'wrong'})]:
            with self.assertRaises(ValueError):
                validate(action, data)

    def test_status_never_returns_credentials_or_field_values(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory))
            try:
                client.state.set('identity', {'company_id': 36, 'user_id': 5, 'company_name': 'Example',
                                              'user_name': 'Employee', 'secret': 'must-not-leak'})
                client.state.set('policy', {'tracking': True, 'policy_expires_at': int(time.time())+3600})
                controller = Controller(client)
                status = json.dumps(controller.snapshot())
                self.assertNotIn('must-not-leak', status)
                self.assertNotIn(client.device['device_secret'], status)
                self.assertNotIn('device_secret', status)
                controller.command('preferences', {'theme': 'dark', 'language': 'ru'})
                self.assertEqual(client.state.get('theme'), 'dark')
                self.assertEqual(controller.snapshot()['language'], 'ru')
            finally:
                client.close()

    def test_already_enrolled_cannot_be_replaced_from_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory))
            try:
                client.state.set('identity', {'company_id': 36})
                with self.assertRaisesRegex(ValueError, 'already_registered'):
                    Controller(client).command('enroll', {'code': 'a'*32, 'key': 'b'*64})
            finally:
                client.close()

    def test_installer_code_is_bound_and_unknown_error_is_localized(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(bundle=Path(directory), code='a'*32)
            with self.assertRaisesRegex(ValueError, 'setup_company_conflict'):
                controller.command('install', {'code': 'b'*32})
            controller.language = 'ru'
            def fail():
                raise RuntimeError('private-network-token')
            controller.task(fail)
            controller.job.join()
            view = controller.snapshot()
            self.assertEqual(view['error'], 'setup_failed')
            self.assertNotIn('private-network-token', json.dumps(view))
            self.assertIn('установку', view['errorText'])

    def test_health_mode_never_enrolls_installs_or_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(bundle=Path(directory), code='a'*32, health=True)
            for action, data in [('install', {'code': 'a'*32}), ('repair', {}), ('retry', {}), ('resume', {})]:
                with self.assertRaises(ValueError):
                    controller.command(action, data)

    def test_explicit_resume_only_clears_local_pause_and_requests_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory))
            try:
                client.state.set('identity', {'company_id': 36, 'user_id': 5})
                client.state.set('policy', {'tracking': True, 'policy_expires_at': int(time.time())+3600,
                                            'domains': ['example.kommo.com'], 'track_processes': ['Tool.exe']})
                client.state.set('paused', True)
                client.outbox.push_payload({'event_id': 'e'*32, 'type': 'web_session',
                                           'timestamp': int(time.time())-30, 'end_timestamp': int(time.time()),
                                           'url': 'https://example.kommo.com/'})
                worker = Mock()
                controller = Controller(client, worker=worker)
                before = dict(client.state.db.execute('SELECT name,value FROM state'))
                pending = client.outbox.batch()
                self.assertEqual(controller.command('status', {})['collection'], 'paused_local')
                self.assertTrue(client.state.get('paused'))
                with patch.object(client.state, 'set', wraps=client.state.set) as write:
                    view = controller.command('resume', {})
                    write.assert_called_once_with('paused', False)
                worker.sync_requested.set.assert_called_once_with()
                self.assertEqual(view['collection'], 'recording')
                after = dict(client.state.db.execute('SELECT name,value FROM state'))
                self.assertEqual({key: value for key, value in before.items() if key != 'paused'},
                                 {key: value for key, value in after.items() if key != 'paused'})
                self.assertEqual(client.outbox.batch(), pending)
                with self.assertRaises(ValueError):
                    controller.command('resume', {})
                worker.sync_requested.set.assert_called_once_with()
            finally:
                client.close()
            reopened = Client(Path(directory))
            try:
                self.assertFalse(reopened.state.get('paused'))
            finally:
                reopened.close()

    def test_resume_preserves_company_employee_and_error_blocks(self):
        cases = [('company_disabled', False, False, 3600), ('employee_disabled', False, False, 3600),
                 ('policy_expired', True, False, -1), ('collection_error', True, True, 3600)]
        for reason, tracking, blocked, lifetime in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                client = Client(Path(directory))
                try:
                    identity = {'company_id': 36, 'user_id': 5}
                    policy = {'tracking': tracking, 'tracking_disabled_reason': reason,
                              'policy_expires_at': int(time.time())+lifetime}
                    client.state.set('identity', identity)
                    client.state.set('policy', policy)
                    client.state.set('paused', True)
                    client.state.set('collection_blocked', blocked)
                    client.state.set('error', 'unauthorized')
                    worker = Mock()
                    view = Controller(client, worker=worker).command('resume', {})
                    self.assertEqual(view['collection'], reason)
                    self.assertFalse(client.policy().get('tracking'))
                    self.assertEqual(client.state.get('policy'), policy)
                    self.assertEqual(client.state.get('identity'), identity)
                    self.assertEqual(client.state.get('collection_blocked'), blocked)
                    self.assertEqual(client.state.get('error'), 'unauthorized')
                    worker.sync_requested.set.assert_called_once_with()
                finally:
                    client.close()

    def test_resume_rejected_in_health_busy_and_unregistered_states(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client(Path(directory))
            try:
                client.state.set('paused', True)
                worker = Mock()
                controller = Controller(client, worker=worker)
                with self.assertRaises(ValueError):
                    controller.command('resume', {})
                client.state.set('identity', {'company_id': 36, 'user_id': 5})
                for health, busy in [(True, False), (False, True)]:
                    controller.health, controller.busy = health, busy
                    with self.assertRaises(ValueError):
                        controller.command('resume', {})
                self.assertTrue(client.state.get('paused'))
                worker.sync_requested.set.assert_not_called()
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
