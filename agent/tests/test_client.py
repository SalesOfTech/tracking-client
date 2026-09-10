import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.client import Client, company_code_from_filename
from agent_tracker.core.event_queue import Event
from agent_tracker.core.files import atomic_json
from agent_tracker.native_host import ALLOWED_ORIGIN, handle, main, read_message, write_message


class FakeHttp:
    def __init__(self):
        self.session = SimpleNamespace(headers={})
        self.calls = []
        self.fail = False

    def post_json(self, path, payload, **kwargs):
        self.calls.append((path, payload))
        if self.fail:
            raise ConnectionError("offline")
        if path.endswith('/enroll'):
            return dict(ok=True,device_id=payload['device_id'],company_id=32,user_id=100,company_name='Test company',user_name='Test employee')
        if path.endswith('/config'):
            return dict(ok=True,device_id=self.device,config=dict(tracking=True,policy_expires_at=int(time.time())+3600))
        return {"ok":True,"ack":{"protocol":3,"device_id":self.device,"event_ids":[e['event_id'] for e in payload['events']]}}


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.http = FakeHttp()
        self.client = Client(Path(self.temp.name), self.http)
        self.http.device = self.client.device['device_id']

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def enroll(self):
        return self.client.enroll('a'*32,'b'*64)

    def test_enrollment_survives_restart_without_employee_key(self):
        identity = self.enroll()
        secret = self.client.device['device_secret']
        self.client.close()
        self.client = Client(Path(self.temp.name), self.http)
        self.assertEqual(identity,self.client.state.get('identity'))
        self.assertEqual(secret,self.client.device['device_secret'])
        stored = self.client.state.db.execute('SELECT value FROM state').fetchall()
        self.assertNotIn('b'*64, json.dumps(stored))
        with self.assertRaises(ValueError):
            self.client.enroll('c'*32,'b'*64)

    def test_failed_enrollment_does_not_pin_invalid_company_code(self):
        self.http.fail=True
        with self.assertRaises(ConnectionError): self.client.enroll('d'*32,'b'*64)
        self.http.fail=False
        self.enroll()

    def test_installer_company_cannot_be_overridden(self):
        install=Path(self.temp.name)/'install'
        atomic_json(install/'enrollment.json',{'company_code':'a'*32})
        with patch.dict('os.environ',{'SOFT_TRACKING_INSTALL':str(install)}):
            with self.assertRaises(ValueError): self.client.enroll('c'*32,'b'*64)
            self.assertEqual([],self.http.calls)
            self.enroll()

    def test_config_updates_names_but_cannot_change_tenant_or_employee(self):
        identity=self.enroll()
        config=dict(ok=True,device_id=identity['device_id'],identity=dict(identity,user_name='Current name'),config=dict(tracking=True,policy_expires_at=int(time.time())+3600))
        with patch.object(self.http,'post_json',return_value=config):
            self.client.refresh_config()
            self.assertEqual('Current name',self.client.state.get('identity')['user_name'])
            config['identity']['company_id']=36
            with self.assertRaises(ValueError):self.client.refresh_config()
            self.assertEqual(32,self.client.state.get('identity')['company_id'])
            self.assertFalse(self.client.policy()['tracking'])

    def test_expired_policy_and_pause_disable_collection_not_delivery(self):
        self.enroll()
        self.client.refresh_config()
        self.assertTrue(self.client.policy()['tracking'])
        self.client.state.set('paused',True)
        self.assertFalse(self.client.policy()['tracking'])
        self.client.outbox.push(Event('session',1700000000,'Tool.exe'))
        self.assertEqual(1,self.client.flush())
        self.client.state.set('paused',False)
        self.client.state.set('policy',{'tracking':True,'policy_expires_at':0})
        self.assertFalse(self.client.policy()['tracking'])

    def test_custody_is_not_database_confirmation(self):
        self.enroll()
        event={'event_id':'e'*32,'type':'navigation','timestamp':1700000000,'url':'https://canva.com/'}
        result=handle(self.client,{'action':'store','events':[event]})
        self.assertEqual([],result['confirmed_event_ids'])
        self.assertEqual(['e'*32],result['stored_event_ids'])
        self.client.flush()
        result=handle(self.client,{'action':'store','events':[event]})
        self.assertEqual(['e'*32],result['confirmed_event_ids'])

    def test_offline_retains_all_events(self):
        self.enroll()
        self.client.outbox.push(Event('session',1700000000,'Tool.exe'))
        self.http.fail=True
        with self.assertRaises(ConnectionError): self.client.flush()
        self.assertEqual(1,self.client.outbox.counts()['pending'])

    def test_browser_receipt_requires_database_ack_and_stores_only_hostname(self):
        self.enroll()
        event = dict(event_id='f'*32, type='web_session', timestamp=1700000000,
                     end_timestamp=1700000042, url='https://example.com/private?token=secret')
        handle(self.client, {'action':'store', 'events':[event]})
        self.assertIsNone(self.client.state.get('last_web_delivery'))
        self.http.fail = True
        with self.assertRaises(ConnectionError): self.client.flush()
        self.assertIsNone(self.client.state.get('last_web_delivery'))
        self.http.fail = False
        self.client.flush()
        receipt = self.client.state.get('last_web_delivery')
        self.assertEqual('example.com', receipt['hostname'])
        self.assertEqual(42, receipt['end_timestamp'] - receipt['timestamp'])
        self.assertNotIn('secret', json.dumps(receipt))

    def test_partial_ack_and_old_retries_do_not_replace_latest_browser_receipt(self):
        self.enroll()
        self.client.state.set('last_web_delivery', {'end_timestamp':1700000100, 'hostname':'new.example'})
        events = [dict(event_id=char*32, type='web_session', timestamp=1700000000,
                       end_timestamp=end, url='https://old.example/')
                  for char,end in [('c',1700000042),('d',1700000200)]]
        for event in events: self.client.outbox.push_payload(event)
        ack = {'ok':True,'ack':{'protocol':3,'device_id':self.http.device,'event_ids':['c'*32]}}
        with patch.object(self.http, 'post_json', return_value=ack): self.client.flush()
        self.assertEqual('new.example', self.client.state.get('last_web_delivery')['hostname'])
        self.assertEqual(1, self.client.outbox.counts()['pending'])

    def test_native_framing_and_origin(self):
        request=io.BytesIO()
        write_message(request,{'action':'status'})
        request.seek(0)
        response=io.BytesIO()
        self.assertEqual(0,main(ALLOWED_ORIGIN,request,response,self.client))
        response.seek(0)
        result=read_message(response)
        self.assertTrue(result['ok'])
        self.assertNotIn('device_secret',json.dumps(result))
        self.assertEqual(2,main('chrome-extension://wrong/',io.BytesIO(),io.BytesIO(),self.client))

    def test_filename_code_is_optional_and_not_company_id(self):
        self.assertEqual('a'*32,company_code_from_filename('TrackingSetup_'+'a'*32+' (1).exe'))
        self.assertEqual('',company_code_from_filename('TrackingSetup_32.exe'))
        self.assertEqual('',company_code_from_filename('Renamed.exe'))


if __name__ == '__main__': unittest.main()
