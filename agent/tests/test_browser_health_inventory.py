import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker.browser_health import connections
from agent_tracker.core.client import Client
from agent_tracker.inventory import clean, linux_apps
from agent_tracker.native_host import handle


class BrowserHealthTests(unittest.TestCase):
    def test_receipt_requires_real_extension_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            client=Client(Path(folder))
            try:
                handle(client, {'action':'status'})
                self.assertEqual([], connections(client))
                reply=handle(client, {'action':'status','browser':{'profile':'a'*32,'version':'3.0.4.60000','family':'Chrome'}})
                self.assertTrue(reply['ok'])
                self.assertTrue(connections(client)[0]['connected'])
                self.assertFalse(connections(client, now=time.time()+180)[0]['connected'])
                with self.assertRaises(ValueError):
                    handle(client, {'action':'status','browser':{'profile':'bad','version':'bad','family':'Chrome'}})
                self.assertNotIn('device_secret', str(reply))
            finally:
                client.close()

    def test_inventory_opt_in_queue_and_daily_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            client=Client(Path(folder))
            try:
                with patch('agent_tracker.inventory.installed_apps', return_value=[{'name':'Excel','executable':'EXCEL.EXE'}]) as collect:
                    client.queue_inventory()
                    collect.assert_not_called()
                    client.state.set('identity', {'company_id':32})
                    client.state.set('policy', {'app_inventory':True, 'policy_expires_at':int(time.time())+500})
                    client.queue_inventory(); client.queue_inventory()
                    self.assertEqual(1, collect.call_count)
                    self.assertEqual(1, client.outbox.counts()['pending'])
                    self.assertEqual('app_inventory', client.outbox.batch()[0]['type'])
                    client.state.set('paused', True)
                    client.state.set('inventory_queued_at', 0)
                    client.queue_inventory()
                    self.assertEqual(1, collect.call_count)
            finally:
                client.close()

    def test_paths_arguments_and_duplicates_do_not_leave_device(self):
        self.assertEqual([{'name':'Editor','executable':'editor.exe'}],clean([
            {'name':'Editor','executable':r'C:\Users\private\Editor\editor.exe'},
            {'name':'Editor','executable':'editor.exe'}]))
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'test.desktop'
            file.write_text('[Desktop Entry]\nType=Application\nName=Text Editor\nExec=/usr/bin/editor --new-window %U\n',encoding='utf-8')
            self.assertEqual([{'name':'Text Editor','executable':'editor'}],clean(linux_apps([folder])))


if __name__=='__main__': unittest.main()
