import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import browser_health, native_host
from agent_tracker.core.client import Client
from test_employee_switch import COMPANY_CODE, OLD_KEY, NEW_KEY, ProfileHttp, event


class BrowserEpochTests(unittest.TestCase):
    def setUp(self):
        no_network = patch('requests.sessions.Session.request', side_effect=AssertionError('Real network forbidden'))
        no_network.start()
        self.addCleanup(no_network.stop)
        no_launch = patch('subprocess.Popen', side_effect=AssertionError('Real launch forbidden'))
        no_launch.start()
        self.addCleanup(no_launch.stop)
        install = patch.dict('os.environ', {'SOFT_TRACKING_INSTALL': ''})
        install.start()
        self.addCleanup(install.stop)
        for name, value in (('system', 'Windows'), ('node', 'test-host')):
            platform_value = patch('agent_tracker.core.client.platform.' + name, return_value=value)
            platform_value.start()
            self.addCleanup(platform_value.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='tracking-browser-epochs-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.http = ProfileHttp()
        self.client = Client(self.root, self.http)
        self.addCleanup(lambda: self.client.close())
        self.client.enroll(COMPANY_CODE, OLD_KEY)
        self.old_epoch = self.client.employee_epoch
        self.old_secret = self.client.device['device_secret']
        self.old_queue = self.client.outbox

    def switch(self):
        candidate = self.client.prepare_employee_switch(COMPANY_CODE, NEW_KEY)
        self.client.activate_employee_switch(candidate['epoch'], expected_epoch=self.old_epoch,
                                             desktop_sessions_closed=True, browser_epoch_ready=True)
        return candidate['epoch']

    def receipt(self, profile='1' * 32, **values):
        return dict(profile=profile, version='3.0.4.12345', family='Chrome', **values)

    def handshake(self, receipt, timestamp=1000):
        with patch.object(browser_health.time, 'time', return_value=timestamp):
            return native_host.handle(self.client, {'action': 'status', 'browser': receipt})

    def ready(self, timestamp=1000):
        with patch.object(browser_health.time, 'time', return_value=timestamp):
            return browser_health.employee_switch_ready(self.client)

    def test_no_recent_browser_passes_but_legacy_handshake_blocks(self):
        self.assertIs(True, self.ready())
        self.handshake(self.receipt())
        self.assertIs(False, self.ready())
        self.handshake(self.receipt(epoch_protocol=1))
        self.assertIs(True, self.ready())
        self.handshake(self.receipt('2' * 32, epoch_protocol=0))
        self.assertIs(False, self.ready())

    def test_freshness_boundary_is_inclusive_and_stale_receipts_are_not_deleted(self):
        self.handshake(self.receipt(epoch_protocol=0))
        self.assertIs(False, self.ready(1000 + browser_health.FRESH_SECONDS))
        self.assertIs(True, self.ready(1001 + browser_health.FRESH_SECONDS))
        self.assertEqual(0, self.client.state.get('browser:' + '1' * 32)['epoch_protocol'])
        rows = browser_health.connections(self.client, now=1001 + browser_health.FRESH_SECONDS)
        self.assertEqual(1, len(rows))
        self.assertFalse(rows[0]['connected'])

    def test_capability_protocol_is_a_strict_integer_and_bad_receipt_cannot_replace_good(self):
        self.handshake(self.receipt(epoch_protocol=1))
        before = self.client.state.get('browser:' + '1' * 32)
        for value in (True, False, 1.0, '1', None, -1, 2, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.handshake(self.receipt(epoch_protocol=value), timestamp=1010)
            self.assertEqual(before, self.client.state.get('browser:' + '1' * 32))
        # Corrupted/older state must not bypass validation through bool == 1.
        self.client.state.set('browser:' + '1' * 32, dict(before, epoch_protocol=True))
        self.assertIs(False, self.ready())

    def test_storage_error_blocks_until_successful_capable_handshake(self):
        self.handshake(self.receipt(epoch_protocol=1, error='storage_error'))
        self.assertIs(False, self.ready())
        self.handshake(self.receipt(epoch_protocol=1, error=''))
        self.assertIs(True, self.ready())

    def test_all_recent_browser_profiles_are_checked_beyond_first_hundred(self):
        for index in range(101):
            self.handshake(self.receipt(f'{index:032x}', epoch_protocol=1))
        self.handshake(self.receipt('f' * 32, epoch_protocol=0))
        self.assertEqual(102, len(browser_health.connections(self.client, now=1000)))
        self.assertIs(False, self.ready())
        self.handshake(self.receipt('f' * 32, epoch_protocol=1))
        self.assertIs(True, self.ready())

    def test_handshake_persists_only_connection_metadata_not_input_history(self):
        receipt = self.receipt(epoch_protocol=1, url='https://private.example.test/',
                               employee_key='secret-value', events=[{'field_value': 'private'}])
        result = self.handshake(receipt)
        stored = self.client.state.get('browser:' + receipt['profile'])
        self.assertEqual({'version', 'family', 'error', 'epoch_protocol', 'last_seen'}, set(stored))
        self.assertEqual(1000, stored['last_seen'])
        public = json.dumps([stored, result])
        self.assertNotIn('private', public)
        self.assertNotIn('secret-value', public)
        self.assertNotIn(self.old_secret, public)

    def test_malformed_receipt_scalar_types_are_validation_errors(self):
        for fields in ({'profile': None}, {'profile': 1}, {'profile': []},
                       {'version': None}, {'version': 1}, {'family': 'Safari'}):
            receipt = dict(self.receipt(epoch_protocol=1), **fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.handshake(receipt)
        self.assertEqual([], browser_health.connections(self.client, now=1000))

    def test_untagged_pre_switch_store_is_explicitly_reported_as_legacy(self):
        payload = event('1', 'web_session')
        result = native_host.handle(self.client, {'action': 'store', 'events': [payload]})
        self.assertEqual(self.old_epoch, result['employee_epoch'])
        self.assertEqual([payload['event_id']], result['stored_event_ids'])
        self.assertEqual([], result['confirmed_event_ids'])
        self.assertEqual([payload], self.old_queue.batch())

    def test_delayed_old_epoch_store_and_receipt_never_touch_new_profile(self):
        new_epoch = self.switch()
        payload = event('2', 'web_session')
        message = {'action': 'store', 'employee_epoch': self.old_epoch, 'events': [payload]}
        call_count = len(self.http.calls)
        result = native_host.handle(self.client, message)
        self.assertEqual(call_count, len(self.http.calls))
        self.assertEqual(self.old_epoch, result['employee_epoch'])
        self.assertEqual([], result['confirmed_event_ids'])
        self.assertEqual([payload], self.old_queue.batch())
        self.assertEqual([], self.client.outbox.batch())
        self.assertEqual(1, self.client.flush())
        self.assertEqual(self.old_secret, self.http.calls[-1][2])
        confirmed = native_host.handle(self.client, message)
        self.assertEqual(self.old_epoch, confirmed['employee_epoch'])
        self.assertEqual([payload['event_id']], confirmed['confirmed_event_ids'])
        self.assertEqual([], self.client.outbox.confirmed([payload['event_id']]))
        self.assertEqual(new_epoch, self.client.employee_epoch)

    def test_delayed_epoch_survives_native_client_restart(self):
        self.switch()
        self.client.close()
        self.client = Client(self.root, self.http)
        payload = event('3', 'web_session')
        result = native_host.handle(self.client, {'action': 'store', 'employee_epoch': self.old_epoch, 'events': [payload]})
        self.assertEqual(self.old_epoch, result['employee_epoch'])
        self.assertEqual([payload], self.client.outbox_for_epoch(self.old_epoch).batch())
        self.assertEqual([], self.client.outbox.batch())

    def test_untagged_store_after_first_switch_cannot_route_to_any_profile(self):
        self.switch()
        for fields in ({}, {'employee_epoch': None}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                native_host.handle(self.client, dict(action='store', events=[event('4', 'web_session')], **fields))
        self.assertEqual([], self.old_queue.batch())
        self.assertEqual([], self.client.outbox.batch())

    def test_unknown_invalid_and_unactivated_epochs_cannot_receive_events(self):
        candidate = self.client.prepare_employee_switch(COMPANY_CODE, NEW_KEY)
        for epoch in (candidate['epoch'], 'f' * 32, '', '../outbox', True, 1, {}, []):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                native_host.handle(self.client, {'action': 'store', 'employee_epoch': epoch,
                                                 'events': [event('5', 'web_session')]})
        self.assertEqual({'pending': 0, 'rejected': 0}, self.client.queue_counts())
        self.assertEqual(self.old_epoch, self.client.employee_epoch)

    def test_per_event_epoch_is_rejected_before_any_payload_is_stored(self):
        payload = event('6', 'web_session')
        tagged = dict(event('7', 'web_session'), employee_epoch=self.old_epoch)
        with self.assertRaises(ValueError):
            native_host.handle(self.client, {'action': 'store', 'employee_epoch': self.old_epoch,
                                             'events': [payload, tagged]})
        self.assertEqual([], self.old_queue.batch())

    def test_rejections_and_same_id_receipts_are_scoped_to_original_epoch(self):
        payload = event('8', 'web_session')
        self.old_queue.push_payload(payload)
        self.old_queue.mark_rejected(payload['event_id'], 'policy_denied')
        new_epoch = self.switch()
        self.client.outbox.push_payload(payload)
        self.client.outbox.acknowledge([payload['event_id']])
        old = native_host.handle(self.client, {'action': 'store', 'employee_epoch': self.old_epoch, 'events': [payload]})
        new = native_host.handle(self.client, {'action': 'store', 'employee_epoch': new_epoch, 'events': [payload]})
        self.assertEqual([], old['confirmed_event_ids'])
        self.assertEqual({payload['event_id']: 'policy_denied'}, old['rejected'])
        self.assertEqual([payload['event_id']], new['confirmed_event_ids'])
        self.assertEqual({}, new['rejected'])
        self.assertEqual({'pending': 0, 'rejected': 1}, self.client.queue_counts())

    def test_wrong_device_or_unsent_acks_never_remove_delayed_browser_payload(self):
        new_epoch = self.switch()
        payload = event('9', 'web_session')
        message = {'action': 'store', 'employee_epoch': self.old_epoch, 'events': [payload]}
        native_host.handle(self.client, message)
        invalid_acks = [{'device_id': new_epoch}, {'event_ids': ['f' * 32]},
                        {'event_ids': [payload['event_id'], payload['event_id']]}, {'protocol': 2}]
        for values in invalid_acks:
            self.http.ack_transform = lambda result, secret: dict(result, ack=dict(result['ack'], **values))
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.client.flush()
            reply = native_host.handle(self.client, message)
            self.assertEqual([], reply['confirmed_event_ids'])
            self.assertEqual([payload], self.old_queue.batch())
            self.assertEqual([], self.client.outbox.batch())

    def test_conflicting_retry_cannot_replace_original_payload(self):
        payload = event('a', 'web_session')
        message = {'action': 'store', 'employee_epoch': self.old_epoch, 'events': [payload]}
        native_host.handle(self.client, message)
        self.switch()
        changed = dict(payload, end_timestamp=1700000099)
        with self.assertRaises(ValueError):
            native_host.handle(self.client, dict(message, events=[changed]))
        self.assertEqual([payload], self.old_queue.batch())
        self.assertEqual([], self.client.outbox.batch())

    def test_authorized_caller_separates_chromium_origin_and_firefox_arguments(self):
        self.assertTrue(native_host.authorized_caller(native_host.ALLOWED_ORIGIN))
        for name in (native_host.HOST_NAME + '.json', native_host.HOST_NAME + '.firefox.json'):
            path = str(self.root / name)
            self.assertTrue(native_host.authorized_caller(path, [native_host.FIREFOX_EXTENSION_ID]))
            self.assertFalse(native_host.authorized_caller(path, []))
            self.assertFalse(native_host.authorized_caller(path, ['wrong@example.test']))
        for origin, arguments in [('chrome-extension://wrong/', []),
                                  (native_host.ALLOWED_ORIGIN + 'extra', []),
                                  ('com.soft.tracking.json', [native_host.FIREFOX_EXTENSION_ID]),
                                  (str(self.root / 'wrong.json'), [native_host.FIREFOX_EXTENSION_ID]),
                                  ('https://example.test/com.soft.tracking.json', [native_host.FIREFOX_EXTENSION_ID])]:
            with self.subTest(origin=origin):
                self.assertFalse(native_host.authorized_caller(origin, arguments))

    def test_native_main_rejects_wrong_origin_before_opening_state(self):
        with patch.object(native_host, 'Client', side_effect=AssertionError('Unauthorized caller opened state')):
            output = io.BytesIO()
            self.assertEqual(2, native_host.main('chrome-extension://wrong/', io.BytesIO(), output, arguments=[]))
            self.assertEqual(b'', output.getvalue())

    def test_firefox_main_status_framing_and_untagged_switch_error_are_safe(self):
        request = io.BytesIO()
        native_host.write_message(request, {'action': 'status'})
        request.seek(0)
        output = io.BytesIO()
        manifest_path = str(self.root / (native_host.HOST_NAME + '.firefox.json'))
        self.assertEqual(0, native_host.main(manifest_path, request, output, self.client,
                                            arguments=[native_host.FIREFOX_EXTENSION_ID]))
        output.seek(0)
        reply = native_host.read_message(output)
        self.assertEqual(self.old_epoch, reply['status']['employee_epoch'])
        self.assertNotIn(self.old_secret, json.dumps(reply))
        self.switch()
        request = io.BytesIO()
        native_host.write_message(request, {'action': 'store', 'events': [event('b', 'web_session')]})
        request.seek(0)
        output = io.BytesIO()
        self.assertEqual(1, native_host.main(native_host.ALLOWED_ORIGIN, request, output, self.client, arguments=[]))
        output.seek(0)
        self.assertEqual({'ok': False, 'error': 'ValueError'}, native_host.read_message(output))
        self.assertEqual({'pending': 0, 'rejected': 0}, self.client.queue_counts())


if __name__ == '__main__':
    unittest.main()
