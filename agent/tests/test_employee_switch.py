import copy
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.client import Client, ClientState
from agent_tracker.core.employee_profiles import EmployeeProfiles
from agent_tracker.core.event_queue import EventQueue


COMPANY_CODE = 'a' * 32
OLD_KEY = 'b' * 64
NEW_KEY = 'c' * 64


def event(char, kind='session'):
    payload = dict(event_id=char * 32, type=kind, timestamp=1700000000)
    if kind == 'web_session':
        payload.update(end_timestamp=1700000060, url='https://example.test/path')
    else:
        payload['exe'] = 'Example.exe'
    return payload


class ProfileHttp:
    """Synthetic enrollment contract; every delivery authenticates a device."""
    def __init__(self):
        self.session = SimpleNamespace(headers={})
        self.devices = {}
        self.calls = []
        self.offline = set()
        self.enrollment_error = None
        self.enrollment_transform = None
        self.ack_transform = None

    def post_json(self, path, payload):
        secret = self.session.headers['Authorization'].removeprefix('Bearer ')
        self.calls.append((path, copy.deepcopy(payload), secret))
        if path.endswith('/enroll'):
            if payload['employee_key'] not in (OLD_KEY, NEW_KEY, 'd' * 64):
                raise ValueError('invalid_enrollment')
            identity = dict(device_id=payload['device_id'], company_id=7,
                            user_id={OLD_KEY: 1, NEW_KEY: 2, 'd' * 64: 3}[payload['employee_key']],
                            company_name='Example company', user_name='Example employee')
            self.devices[secret] = identity
            if self.enrollment_error:
                raise self.enrollment_error
            result = dict(ok=True, **identity)
            return self.enrollment_transform(result) if self.enrollment_transform else result
        if secret in self.offline:
            raise ConnectionError('offline')
        identity = self.devices[secret]
        if path.endswith('/config'):
            return dict(ok=True, device_id=identity['device_id'], identity=identity,
                        config=dict(tracking=True, app_inventory=True, policy_expires_at=int(time.time()) + 3600))
        result = dict(ok=True, ack=dict(protocol=3, device_id=identity['device_id'],
                                       event_ids=[row['event_id'] for row in payload['events']]))
        return self.ack_transform(result, secret) if self.ack_transform else result


class EmployeeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.no_network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.http = ProfileHttp()
        self.client = Client(self.root, self.http)
        self.old_identity = self.client.enroll(COMPANY_CODE, OLD_KEY)
        self.old_epoch = self.client.employee_epoch
        self.old_device = self.client.device
        self.old_outbox = self.client.outbox

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def prepare(self, key=NEW_KEY):
        return self.client.prepare_employee_switch(COMPANY_CODE, key)

    def activate(self, candidate):
        return self.client.activate_employee_switch(candidate['epoch'], expected_epoch=candidate['expected_epoch'],
                                                   desktop_sessions_closed=True, browser_epoch_ready=True)

    def restart(self):
        self.client.close()
        self.client = Client(self.root, self.http)

    def test_prepare_keeps_old_identity_auth_policy_and_queue(self):
        self.client.refresh_config()
        before = self.client.state.get('policy')
        self.old_outbox.push_payload(event('1'))
        prepared = self.prepare()
        self.assertEqual(self.old_identity, self.client.state.get('identity'))
        self.assertEqual(before, self.client.state.get('policy'))
        self.assertEqual(self.old_device, self.client.device)
        self.assertIs(self.old_outbox, self.client.outbox)
        self.assertNotEqual(self.old_epoch, prepared['epoch'])
        self.assertEqual(self.old_epoch, prepared['expected_epoch'])
        self.assertEqual('Bearer ' + self.old_device['device_secret'], self.http.session.headers['Authorization'])
        self.assertEqual(1, self.client.flush())
        self.assertEqual(self.old_device['device_secret'], self.http.calls[-1][2])

    def test_uncertain_enrollment_reuses_candidate_credentials_after_restart(self):
        self.http.enrollment_error = ConnectionError('response lost')
        with self.assertRaises(ConnectionError):
            self.prepare()
        first_payload = self.http.calls[-1][1]
        self.assertEqual(self.old_epoch, self.client.employee_epoch)
        self.restart()
        self.http.enrollment_error = None
        prepared = self.prepare()
        self.assertEqual(first_payload, self.http.calls[-1][1])
        self.assertEqual(first_payload['device_id'], prepared['epoch'])
        call_count = len(self.http.calls)
        self.assertEqual(prepared, self.prepare())
        self.assertEqual(call_count, len(self.http.calls))

    def test_bad_key_does_not_change_active_state_or_old_events(self):
        self.old_outbox.push_payload(event('2'))
        before = self.client.status()
        with self.assertRaises(ValueError):
            self.prepare('e' * 64)
        self.assertEqual(before, self.client.status())
        self.assertEqual([event('2')], self.old_outbox.batch())
        self.assertEqual(self.old_device, self.client.device)

    def test_same_company_is_checked_before_network_and_in_response(self):
        count = len(self.http.calls)
        with self.assertRaises(ValueError):
            self.client.prepare_employee_switch('f' * 32, NEW_KEY)
        self.assertEqual(count, len(self.http.calls))
        self.http.enrollment_transform = lambda result: dict(result, company_id=8)
        with self.assertRaisesRegex(ValueError, 'same company'):
            self.prepare()
        self.assertEqual(self.old_identity, self.client.state.get('identity'))
        candidates = self.client.state.db.execute("SELECT identity FROM employee_profiles WHERE status='candidate'").fetchall()
        self.assertEqual([(None,)], candidates)

    def test_invalid_enrollment_responses_never_activate(self):
        for values in ({'device_id': 'f' * 32}, {'company_id': True}, {'user_id': 0},
                       {'user_name': None}, {'ok': False}):
            self.http.enrollment_transform = lambda result: dict(result, **values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.prepare()
            self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_same_employee_cannot_activate_a_duplicate_device(self):
        with self.assertRaisesRegex(ValueError, 'already active'):
            self.prepare(OLD_KEY)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_barriers_are_required_and_unknown_epochs_fail_closed(self):
        candidate = self.prepare()
        for barriers in ({}, {'desktop_sessions_closed': True}, {'browser_epoch_ready': True},
                         {'desktop_sessions_closed': 1, 'browser_epoch_ready': True}):
            with self.subTest(barriers=barriers), self.assertRaises(ValueError):
                self.client.activate_employee_switch(candidate['epoch'], expected_epoch=self.old_epoch, **barriers)
        for epoch in (candidate['epoch'], 'f' * 32, '../outbox', ''):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                self.client.outbox_for_epoch(epoch)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_activation_is_atomic_and_resets_employee_policy_not_pause(self):
        self.client.refresh_config()
        self.client.state.set('paused', True)
        self.client.state.set('inventory_queued_at', 1700000000)
        self.client.state.set('last_web_delivery', {'hostname': 'old.example.test'})
        self.client.state.set('last_delivery_at', 1700000000)
        prepared = self.prepare()
        identity = self.activate(prepared)
        self.assertEqual(2, identity['user_id'])
        self.assertEqual(prepared['epoch'], self.client.device['device_id'])
        self.assertEqual({}, self.client.state.get('policy'))
        self.assertTrue(self.client.state.get('paused'))
        self.assertEqual(0, self.client.state.get('inventory_queued_at'))
        self.assertEqual({}, self.client.state.get('last_web_delivery'))
        self.assertEqual(0, self.client.state.get('last_delivery_at'))
        self.assertFalse(self.client.policy()['tracking'])
        self.restart()
        self.assertEqual(identity, self.client.state.get('identity'))
        self.assertEqual(prepared['epoch'], self.client.employee_epoch)
        self.assertEqual(identity, self.activate(prepared))

    def test_interrupted_activation_rolls_back_all_state_and_is_retryable(self):
        candidate = self.prepare()
        before = self.client.state.db.execute('SELECT name,value FROM state ORDER BY name').fetchall()
        original = EmployeeProfiles._set

        def crash(db, key, value):
            original(db, key, value)
            if key == 'identity':
                raise OSError('simulated durable storage failure')

        with patch.object(EmployeeProfiles, '_set', side_effect=crash), self.assertRaises(OSError):
            self.activate(candidate)
        self.restart()
        after = self.client.state.db.execute('SELECT name,value FROM state ORDER BY name').fetchall()
        self.assertEqual(before, after)
        self.assertEqual('candidate', self.client.profiles.get(candidate['epoch'])['status'])
        self.assertEqual('active', self.client.profiles.get(self.old_epoch)['status'])
        self.activate(candidate)

    def test_outbox_creation_failure_does_not_activate(self):
        candidate = self.prepare()
        with patch('agent_tracker.core.client.EventQueue', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.activate(candidate)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)
        self.activate(candidate)

    def test_process_exit_during_activation_preserves_old_pointer(self):
        candidate = self.prepare()
        self.old_outbox.push_payload(event('1'))
        self.client.close()
        script = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from agent_tracker.core.client import Client
from agent_tracker.core.employee_profiles import EmployeeProfiles
client = Client(Path(sys.argv[2]), SimpleNamespace(session=SimpleNamespace(headers={})))
original = EmployeeProfiles._set
def crash(db, name, value):
    original(db, name, value)
    if name == 'identity':
        os._exit(23)
EmployeeProfiles._set = staticmethod(crash)
client.activate_employee_switch(sys.argv[3], expected_epoch=sys.argv[4],
    desktop_sessions_closed=True, browser_epoch_ready=True)
"""
        try:
            result = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1]),
                                     str(self.root), candidate['epoch'], self.old_epoch],
                                    capture_output=True, text=True, timeout=15)
        finally:
            self.client = Client(self.root, self.http)
        self.assertEqual(23, result.returncode, result.stderr)
        self.assertEqual(self.old_epoch, self.client.employee_epoch)
        self.assertEqual(self.old_device, self.client.device)
        self.assertEqual([event('1')], self.client.outbox.batch())
        self.activate(candidate)

    def test_http_session_auth_is_serialized_during_old_queue_delivery(self):
        self.old_outbox.push_payload(event('2'))
        self.activate(self.prepare())
        entered = threading.Event()
        release = threading.Event()
        errors = []
        original = self.http.post_json

        def request(path, payload):
            if path.endswith('/events'):
                secret_before = self.http.session.headers['Authorization']
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test delivery barrier')
                self.assertEqual(secret_before, self.http.session.headers['Authorization'])
            return original(path, payload)

        def call(operation):
            try:
                operation()
            except BaseException as error:
                errors.append(error)

        delivery = threading.Thread(target=call, args=(self.client.flush,))
        config = threading.Thread(target=call, args=(self.client.refresh_config,))
        with patch.object(self.http, 'post_json', side_effect=request):
            delivery.start()
            try:
                self.assertTrue(entered.wait(5))
                config.start()
            finally:
                release.set()
                delivery.join(5)
                if config.ident is not None:
                    config.join(5)
        self.assertFalse(delivery.is_alive())
        self.assertFalse(config.is_alive())
        self.assertEqual([], errors)
        calls = [call for call in self.http.calls if not call[0].endswith('/enroll')]
        self.assertEqual(self.old_device['device_secret'], calls[0][2])
        self.assertEqual(self.client.device['device_secret'], calls[1][2])

    def test_old_and_new_events_keep_distinct_credentials_across_restart(self):
        self.old_outbox.push_payload(event('1'))
        candidate = self.prepare()
        self.activate(candidate)
        new_device = self.client.device
        self.client.outbox.push_payload(event('2'))
        self.old_outbox.push_payload(event('3', 'web_session'))
        self.assertEqual(3, self.client.status()['queue']['pending'])
        self.assertEqual([event('1'), event('3', 'web_session')], self.old_outbox.batch())
        self.restart()
        self.assertEqual(3, self.client.flush())
        calls = [call for call in self.http.calls if call[0].endswith('/events')]
        self.assertEqual([event('1'), event('3', 'web_session')], calls[0][1]['events'])
        self.assertEqual(self.old_device['device_secret'], calls[0][2])
        self.assertEqual([event('2')], calls[1][1]['events'])
        self.assertEqual(new_device['device_secret'], calls[1][2])
        self.assertEqual({}, self.client.state.get('last_web_delivery'))

    def test_rejected_old_rows_and_receipts_are_retained_and_aggregate(self):
        self.old_outbox.push_payload(event('4'))
        self.old_outbox.push_payload(event('5'))
        self.old_outbox.mark_rejected('4' * 32, 'policy_denied')
        self.old_outbox.acknowledge(['5' * 32])
        self.activate(self.prepare())
        self.client.outbox.push_payload(event('6'))
        self.assertEqual({'pending': 1, 'rejected': 1}, self.client.status()['queue'])
        self.assertEqual({'4' * 32: 'policy_denied'}, self.old_outbox.rejections(['4' * 32]))
        self.assertEqual(['5' * 32], self.old_outbox.confirmed(['5' * 32]))
        self.assertEqual([], self.client.outbox.confirmed(['5' * 32]))
        self.client.retry_rejected()
        self.assertEqual({'pending': 2, 'rejected': 0}, self.client.queue_counts())
        self.assertEqual([event('4')], self.old_outbox.batch())

    def test_old_profile_failure_does_not_starve_new_profile_or_reassign(self):
        self.old_outbox.push_payload(event('7'))
        self.activate(self.prepare())
        self.client.outbox.push_payload(event('8'))
        self.http.offline.add(self.old_device['device_secret'])
        with self.assertRaises(ConnectionError):
            self.client.flush()
        self.assertEqual([event('7')], self.old_outbox.batch())
        self.assertEqual([], self.client.outbox.batch())
        self.assertEqual(['8' * 32], self.client.outbox.confirmed(['8' * 32]))

    def test_wrong_device_ack_never_removes_old_events(self):
        self.old_outbox.push_payload(event('9'))
        self.activate(self.prepare())
        active_epoch = self.client.employee_epoch
        self.http.ack_transform = lambda result, secret: dict(result, ack=dict(result['ack'], device_id=active_epoch))
        with self.assertRaises(ValueError):
            self.client.flush()
        self.assertEqual([event('9')], self.old_outbox.batch())

    def test_delayed_native_rows_use_explicit_original_epoch(self):
        self.activate(self.prepare())
        with self.assertRaises(ValueError):
            self.client.outbox_for_epoch(None)
        queue = self.client.outbox_for_epoch(self.old_epoch)
        queue.push_payload(event('a', 'web_session'))
        self.assertIs(self.old_outbox, queue)
        self.assertEqual([], self.client.outbox.batch())
        self.assertEqual(1, self.client.flush())
        self.assertEqual(self.old_device['device_secret'], self.http.calls[-1][2])

    def test_stale_candidate_cannot_override_later_switch(self):
        first = self.prepare()
        stale = self.prepare('d' * 64)
        self.activate(first)
        with self.assertRaises(ValueError):
            self.activate(stale)
        self.assertEqual(first['epoch'], self.client.employee_epoch)

    def test_other_client_observes_atomic_pointer_but_held_queue_stays_old(self):
        other = Client(self.root, ProfileHttp())
        try:
            held_queue = other.outbox
            candidate = self.prepare()
            self.activate(candidate)
            self.assertEqual(candidate['epoch'], other.employee_epoch)
            self.assertEqual(candidate['epoch'], other.device['device_id'])
            self.assertNotEqual(held_queue.path, other.outbox.path)
            held_queue.push_payload(event('b'))
            self.assertEqual([event('b')], self.old_outbox.batch())
        finally:
            other.close()

    def test_stale_config_response_cannot_restore_old_identity_or_policy(self):
        candidate = self.prepare()
        other = Client(self.root, ProfileHttp())
        original = self.http.post_json

        def during_request(path, payload):
            result = original(path, payload)
            other.activate_employee_switch(candidate['epoch'], expected_epoch=self.old_epoch,
                                           desktop_sessions_closed=True, browser_epoch_ready=True)
            return result

        try:
            with patch.object(self.http, 'post_json', side_effect=during_request), self.assertRaises(ValueError):
                self.client.refresh_config()
            self.assertEqual(candidate['identity'], self.client.state.get('identity'))
            self.assertEqual({}, self.client.state.get('policy'))
        finally:
            other.close()

    def test_config_after_activation_authenticates_only_new_employee(self):
        self.activate(self.prepare())
        self.client.refresh_config()
        self.assertEqual(self.client.device['device_secret'], self.http.calls[-1][2])
        self.assertTrue(self.client.policy()['tracking'])

    def test_inventory_started_before_switch_stays_with_old_profile(self):
        self.client.refresh_config()
        candidate = self.prepare()
        other = Client(self.root, ProfileHttp())

        def collect():
            other.activate_employee_switch(candidate['epoch'], expected_epoch=self.old_epoch,
                                           desktop_sessions_closed=True, browser_epoch_ready=True)
            return [{'name': 'Example app', 'executable': 'Example.exe'}]

        try:
            with patch('agent_tracker.inventory.installed_apps', side_effect=collect):
                self.client.queue_inventory()
            self.assertEqual(1, self.old_outbox.counts()['pending'])
            self.assertEqual([], self.client.outbox.batch())
            self.assertEqual(0, self.client.state.get('inventory_queued_at'))
            self.assertEqual({}, self.client.state.get('policy'))
        finally:
            other.close()

    def test_stale_auth_failure_cannot_clear_new_employee_policy(self):
        candidate = self.prepare()
        other = Client(self.root, ProfileHttp())
        new_policy = dict(tracking=True, policy_expires_at=int(time.time()) + 3600)

        def during_request(path, payload):
            other.activate_employee_switch(candidate['epoch'], expected_epoch=self.old_epoch,
                                           desktop_sessions_closed=True, browser_epoch_ready=True)
            other.state.set('policy', new_policy)
            error = ConnectionError('old device revoked')
            error.response = SimpleNamespace(status_code=403)
            raise error

        try:
            with patch.object(self.http, 'post_json', side_effect=during_request), self.assertRaises(ConnectionError):
                self.client.refresh_config()
            self.assertEqual(new_policy, self.client.state.get('policy'))
            self.assertEqual(candidate['identity'], self.client.state.get('identity'))
        finally:
            other.close()

    def test_employee_keys_and_secrets_are_not_in_public_status(self):
        candidate = self.prepare()
        self.activate(candidate)
        rows = json.dumps(self.client.state.db.execute('SELECT value FROM state').fetchall())
        rows += json.dumps(self.client.state.db.execute('SELECT * FROM employee_profiles').fetchall())
        self.assertNotIn(OLD_KEY, rows)
        self.assertNotIn(NEW_KEY, rows)
        public = json.dumps([candidate, self.client.status()])
        for secret in (self.old_device['device_secret'], self.client.device['device_secret'], OLD_KEY, NEW_KEY):
            self.assertNotIn(secret, public)

    def test_multiple_switches_preserve_every_profile(self):
        for char, key in [('c', NEW_KEY), ('d', 'd' * 64), ('e', OLD_KEY)]:
            self.client.outbox.push_payload(event(char))
            self.activate(self.prepare(key))
        self.assertEqual(1, self.client.state.get('identity')['user_id'])
        self.assertEqual(3, self.client.queue_counts()['pending'])
        self.assertEqual(4, len(self.client.profiles.delivery_profiles()))
        self.assertEqual(3, self.client.flush())


class LegacyMigrationTests(unittest.TestCase):
    def test_existing_state_and_queue_are_adopted_without_moving_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = ClientState(root)
            device = state.get('device')
            identity = dict(device_id=device['device_id'], company_id=7, user_id=1,
                            company_name='Example company', user_name='Example employee')
            state.set('identity', identity)
            state.close()
            queue = EventQueue(root / 'outbox.sqlite3')
            queue.push_payload(event('f'))
            queue.close()
            client = Client(root, ProfileHttp())
            try:
                self.assertEqual(device['device_id'], client.employee_epoch)
                self.assertEqual(device['device_id'], client.status()['legacy_employee_epoch'])
                self.assertEqual(root / 'outbox.sqlite3', client.outbox.path)
                self.assertEqual([event('f')], client.outbox.batch())
                self.assertEqual(identity, client.state.get('identity'))
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
