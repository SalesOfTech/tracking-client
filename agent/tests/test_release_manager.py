import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from nacl.signing import SigningKey

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.files import atomic_json,read_json
from agent_tracker.core.release_manager import ReleaseManager,executable_name
from agent_tracker.installer import install, InstallerError
from agent_tracker.core.client import ClientState
from agent_tracker.core.event_queue import EventQueue
from agent_tracker.core.instance import SingleInstance
from agent_tracker.core.target import runtime_target


class Response:
    status_code=200
    def __init__(self,raw): self.raw=raw
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def iter_content(self,size):
        for offset in range(0,len(self.raw),size): yield self.raw[offset:offset+size]


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        migration = patch('agent_tracker.legacy_migration.replace_current_user')
        self.migration = migration.start()
        self.addCleanup(migration.stop)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'install';self.root.mkdir()
        self.key=SigningKey.generate()
        public=base64.b64encode(bytes(self.key.verify_key)).decode()
        self.keys={'test':public}
        atomic_json(self.root/'trusted-update-keys.json',self.keys)
        atomic_json(self.root/'current.json',{'version':'3.0.0'})
        system,arch=runtime_target()
        self.old={'version':'3.0.0','os':system,'architecture':arch,'target':system+'-'+arch+'-modern','launcher_protocol':1}
        atomic_json(self.root/'versions/3.0.0/build.json',self.old)
        self.extension={'key':'stable-public-key','permissions':['storage'],'version':'3.0.0'}
        atomic_json(self.root/'extension/manifest.json',self.extension)
        (self.root/'outbox-sentinel').write_text('must survive')
    def tearDown(self): self.tmp.cleanup()
    def package(self,version='3.0.1',permissions=None):
        build=dict(self.old,version=version)
        archive=Path(self.tmp.name)/('release-'+version+'.zip')
        extension=dict(self.extension,version=version)
        if permissions is not None: extension['permissions']=permissions
        with zipfile.ZipFile(archive,'w') as package:
            package.writestr('build.json',json.dumps(build))
            package.writestr('extension/manifest.json',json.dumps(extension))
            for native in (False,True):
                package.writestr('app/'+executable_name(native),b'executable fixture')
                package.writestr('launcher/'+executable_name(native,True),b'launcher fixture')
        raw=archive.read_bytes()
        manifest=dict(build,protocol=3,expires_at=int(time.time())+3600,sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),url='https://tracking.salesoftech.com/test.zip')
        encoded=json.dumps(manifest).encode()
        envelope={'key_id':'test','payload':base64.b64encode(encoded).decode(),'signature':base64.b64encode(self.key.sign(encoded).signature).decode()}
        return archive,manifest,envelope
    def test_activate_confirm_and_keep_local_data(self):
        archive,manifest,_=self.package()
        manager=ReleaseManager(self.root)
        staged=manager.stage(archive,manifest);manager.activate(staged);manager.confirm()
        self.assertEqual('3.0.1',manager.active()['version'])
        self.assertEqual('3.0.1',read_json(self.root/'extension/manifest.json')['version'])
        self.assertFalse((self.root/'pending.json').exists())
        self.assertEqual('must survive',(self.root/'outbox-sentinel').read_text())

    def test_installer_upgrades_offline_and_rolls_back_failed_health(self):
        from agent_tracker.installer import upgrade_existing
        for native in (False,True):
            (self.root/executable_name(native,True)).write_bytes(b'launcher')
        old_app=self.root/'versions/3.0.0/app'/executable_name()
        old_app.parent.mkdir();old_app.write_bytes(b'old application')
        atomic_json(self.root/'enrollment.json',{'company_code':'a'*32})
        bundle=Path(self.tmp.name)/'bundle';bundle.mkdir()
        archive,manifest,envelope=self.package()
        (bundle/'release.zip').write_bytes(archive.read_bytes())
        atomic_json(bundle/'manifest.json',envelope)
        def fail(*_): raise ValueError('health failed')
        with self.assertRaisesRegex(InstallerError,'setup_upgrade_failed'):
            upgrade_existing(bundle,self.root,integrate=False,health_check=fail)
        self.assertEqual('3.0.0',ReleaseManager(self.root).active()['version'])
        upgrade_existing(bundle,self.root,integrate=False,health_check=lambda *_:None)
        self.assertEqual('3.0.1',ReleaseManager(self.root).active()['version'])
        self.assertEqual('a'*32,read_json(self.root/'enrollment.json')['company_code'])
        self.assertEqual('must survive',(self.root/'outbox-sentinel').read_text())

    def test_installer_does_not_force_stop_an_unresponsive_client(self):
        from agent_tracker.installer import stopped_supervisor
        with SingleInstance(self.root/'supervisor.lock'):
            with self.assertRaisesRegex(InstallerError,'setup_close_required'):
                with stopped_supervisor(self.root,timeout=0):
                    self.fail('Entered active supervisor lock')
        self.assertEqual('3.0.0',ReleaseManager(self.root).active()['version'])
        self.assertEqual('must survive',(self.root/'outbox-sentinel').read_text())
    def test_crash_journal_rolls_back_extension_and_agent(self):
        archive,manifest,_=self.package();manager=ReleaseManager(self.root)
        manager.activate(manager.stage(archive,manifest))
        ReleaseManager(self.root).rollback()
        self.assertEqual('3.0.0',manager.active()['version'])
        self.assertEqual('3.0.0',read_json(self.root/'extension/manifest.json')['version'])
        self.assertEqual('3.0.1',read_json(self.root/'failed.json')['version'])
    def test_permission_escalation_rejected(self):
        archive,manifest,_=self.package(permissions=['storage','debugger'])
        with self.assertRaises(ValueError):ReleaseManager(self.root).stage(archive,manifest)
        self.assertEqual('3.0.0',ReleaseManager(self.root).active()['version'])

    def test_confirmed_update_does_not_roll_back_after_journal_cleanup_crash(self):
        archive,manifest,_=self.package();manager=ReleaseManager(self.root)
        manager.activate(manager.stage(archive,manifest))
        atomic_json(self.root/'last-good.json',{'version':'3.0.1','previous_version':'3.0.0'})
        manager.rollback()
        self.assertEqual('3.0.1',manager.active()['version'])
        self.assertFalse((self.root/'pending.json').exists())

    def test_update_never_sends_device_credentials_to_another_origin(self):
        _,manifest,_=self.package()
        manifest['url']='https://other.example/release.zip'
        with self.assertRaises(ValueError):ReleaseManager(self.root).download(manifest)
    def test_bounded_download_and_signed_manifest(self):
        archive,manifest,envelope=self.package()
        class Session:
            def get(self,url,**kwargs):return Response(json.dumps(envelope).encode() if url.endswith('manifest.json') else archive.read_bytes())
        manager=ReleaseManager(self.root,Session())
        checked=manager.check();self.assertEqual('3.0.1',checked['version'])
        self.assertEqual(archive.read_bytes(),manager.download(checked).read_bytes())
        manifest['size']=1
        with self.assertRaises(ValueError):manager.download(manifest)
    def installer_fixture(self):
        archive,manifest,envelope=self.package()
        bundle=Path(self.tmp.name)/'setup';bundle.mkdir()
        (bundle/'release.zip').write_bytes(archive.read_bytes())
        atomic_json(bundle/'manifest.json',envelope)
        atomic_json(bundle/'trusted-update-keys.json',self.keys)
        atomic_json(bundle/'setup-build.json',manifest)
        root=Path(self.tmp.name)/'new-install'
        install(bundle,root,'a'*32,integrate=False)
        return bundle, root

    def test_install_profile_and_wrong_company_reinstall(self):
        bundle,root=self.installer_fixture()
        self.assertEqual('a'*32,read_json(root/'enrollment.json')['company_code'])
        self.assertTrue((root/executable_name(True,True)).exists())
        with self.assertRaisesRegex(InstallerError,'setup_company_conflict'):
            install(bundle,root,'b'*32,integrate=False,company_resolver=lambda code:32 if code=='a'*32 else 36)
        self.assertEqual('a'*32,read_json(root/'enrollment.json')['company_code'])

    def test_fresh_install_registers_real_startup_with_stable_launcher(self):
        from agent_tracker import integration
        bundle, _ = self.installer_fixture()
        root = Path(self.tmp.name) / 'fresh-install'
        startup = Path(self.tmp.name) / 'login' / 'soft.desktop'
        with patch('agent_tracker.installer.protect_workspace'), patch('agent_tracker.installer.register_host'), \
                patch('agent_tracker.installer.shortcuts'), patch.object(integration, '_system', return_value='linux'), \
                patch.object(integration, '_startup_path', return_value=startup):
            launcher = install(bundle, root, 'a'*32)
            self.migration.assert_called_once_with(root.resolve())
            self.assertTrue(integration.autostart_status(launcher)['registered'])
            self.assertIn('--autostart', startup.read_text())

    def test_same_version_setup_repairs_startup_without_stopping_or_replacing_data(self):
        from agent_tracker import integration
        bundle, root = self.installer_fixture()
        startup = Path(self.tmp.name) / 'login' / 'soft.desktop'
        before = (root/'enrollment.json').read_bytes(), (root/'current.json').read_bytes()
        with patch.object(integration, '_system', return_value='linux'), \
                patch.object(integration, '_startup_path', return_value=startup), \
                patch('agent_tracker.installer.stopped_supervisor') as stop, SingleInstance(root/'supervisor.lock'):
            launcher = install(bundle, root, 'a'*32)
            self.assertTrue(integration.autostart_status(launcher)['registered'])
            stop.assert_not_called()
        self.assertEqual(before, ((root/'enrollment.json').read_bytes(), (root/'current.json').read_bytes()))

    def test_existing_upgrade_registers_startup_but_nonintegrated_setup_does_not(self):
        bundle, root = self.installer_fixture()
        with patch('agent_tracker.installer.autostart') as startup:
            install(bundle, root, 'a'*32, integrate=False)
            startup.assert_not_called()
            archive, _, envelope = self.package('3.0.2')
            (bundle/'release.zip').write_bytes(archive.read_bytes())
            atomic_json(bundle/'manifest.json', envelope)
            launcher = install(bundle, root, 'a'*32, health_check=lambda *_: None)
            startup.assert_called_once_with(launcher, True)
        self.assertEqual(read_json(root/'current.json')['version'], '3.0.2')

    def test_repeated_download_opens_running_installation_without_replacing_data(self):
        bundle,root=self.installer_fixture()
        state=ClientState(root.parent)
        identity={'company_id':32,'user_id':123}
        state.set('identity',identity);state.set('company_code_hash',hashlib.sha256(('a'*32).encode()).hexdigest())
        device=state.get('device');state.close()
        event={'event_id':'e'*32,'type':'navigation','timestamp':1700000000,'url':'https://example.test/'}
        queue=EventQueue(root.parent/'outbox.sqlite3');queue.push_payload(event);queue.close()
        before=(root/'enrollment.json').read_bytes(),(root/'current.json').read_bytes()
        calls=[]
        def resolve(code):
            calls.append(code)
            return 32
        with SingleInstance(root/'supervisor.lock'):
            launcher=install(bundle,root,'b'*32,integrate=False,company_resolver=resolve)
        self.assertTrue(launcher.samefile(root/executable_name(False,True)))
        self.assertEqual(['b'*32],calls)
        self.assertEqual(before,((root/'enrollment.json').read_bytes(),(root/'current.json').read_bytes()))
        state=ClientState(root.parent)
        self.assertEqual(identity,state.get('identity'));self.assertEqual(device,state.get('device'))
        self.assertEqual(hashlib.sha256(('a'*32).encode()).hexdigest(),state.get('company_code_hash'));state.close()
        queue=EventQueue(root.parent/'outbox.sqlite3');self.assertEqual([event],queue.batch());queue.close()

    def test_unregistered_alias_checks_both_codes_and_same_code_works_offline(self):
        bundle,root=self.installer_fixture()
        calls=[]
        def resolve(code):
            calls.append(code)
            return 32
        install(bundle,root,'b'*32,integrate=False,company_resolver=resolve)
        self.assertEqual(['a'*32,'b'*32],calls)
        def offline(code):
            raise InstallerError('setup_company_unavailable')
        install(bundle,root,'a'*32,integrate=False,company_resolver=offline)
        with self.assertRaisesRegex(InstallerError,'setup_company_unavailable'):
            install(bundle,root,'b'*32,integrate=False,company_resolver=offline)
        self.assertEqual('a'*32,read_json(root/'enrollment.json')['company_code'])


if __name__=='__main__':unittest.main()
