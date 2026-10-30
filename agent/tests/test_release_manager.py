import base64
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from nacl.signing import SigningKey
# Load the host backend before tests temporarily simulate macOS through sys.platform.
import psutil

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
        registration = patch('agent_tracker.installer.register_uninstaller')
        self.registration = registration.start()
        self.addCleanup(registration.stop)
        update_registration = patch('agent_tracker.uninstaller.register_uninstaller')
        self.update_registration = update_registration.start()
        self.addCleanup(update_registration.stop)
        migration = patch('agent_tracker.legacy_migration.replace_current_user')
        self.migration = migration.start()
        self.addCleanup(migration.stop)
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()/'install';self.root.mkdir()
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
    def package(self,version='3.0.1',permissions=None,engine=None):
        build=dict(self.old,version=version)
        archive=Path(self.tmp.name).resolve()/('release-'+version+'.zip')
        extension=dict(self.extension,version=version)
        if permissions is not None: extension['permissions']=permissions
        with zipfile.ZipFile(archive,'w') as package:
            package.writestr('build.json',json.dumps(build))
            package.writestr('extension/manifest.json',json.dumps(extension))
            for native in (False,True):
                package.writestr('app/'+executable_name(native),b'executable fixture')
                entry = zipfile.ZipInfo('launcher/' + executable_name(native, True))
                entry.external_attr = (stat.S_IFREG | 0o755) << 16
                package.writestr(entry, b'launcher fixture' + (hashlib.sha256(engine).digest() if engine is not None else b''))
            if engine is not None:
                name = 'engine-' + hashlib.sha256(engine).hexdigest() + '.zip'
                for folder in ('app', 'launcher'):
                    package.writestr(folder + '/' + name, engine)
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
        bundle=Path(self.tmp.name).resolve()/'bundle';bundle.mkdir()
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

    def test_rollbacks_retry_with_persistent_capped_backoff_and_confirm_clears_it(self):
        archive, manifest, envelope = self.package()
        class Session:
            def get(self, url, **kwargs): return Response(json.dumps(envelope).encode())
        now = int(time.time())
        for attempt, delay in enumerate((900, 3600, 14400, 86400, 86400), 1):
            with patch('agent_tracker.core.release_manager.time.time', return_value=now):
                manager = ReleaseManager(self.root, Session())
                manager.activate(manager.stage(archive, manifest))
                manager.rollback()
                failure = read_json(self.root / 'failed.json')
                self.assertEqual(attempt, failure['attempts'])
                self.assertEqual(now + delay, failure['retry_at'])
                self.assertIsNone(ReleaseManager(self.root, Session()).check())
            now += delay
            # Refresh the signed manifest as a release server does after its expiry.
            manifest['expires_at'] = now + 3600
            encoded = json.dumps(manifest).encode()
            envelope = {'key_id': 'test', 'payload': base64.b64encode(encoded).decode(),
                        'signature': base64.b64encode(self.key.sign(encoded).signature).decode()}
            with patch('time.time', return_value=now):
                self.assertEqual('3.0.1', ReleaseManager(self.root, Session()).check()['version'])
        manager.activate(manager.stage(archive, manifest))
        manager.confirm()
        self.assertFalse((self.root / 'failed.json').exists())

    def test_legacy_failed_marker_retries_and_new_version_is_not_blocked(self):
        _, _, envelope = self.package()
        class Session:
            def get(self, url, **kwargs): return Response(json.dumps(envelope).encode())
        atomic_json(self.root / 'failed.json', {'version': '3.0.1', 'time': int(time.time())})
        self.assertEqual('3.0.1', ReleaseManager(self.root, Session()).check()['version'])
        atomic_json(self.root / 'failed.json', {'version': '3.0.1', 'retry_at': int(time.time()) + 86400, 'attempts': 4})
        _, _, envelope = self.package('3.0.2')
        self.assertEqual('3.0.2', ReleaseManager(self.root, Session()).check()['version'])

    def test_repeated_failed_attempts_do_not_accumulate_known_artifacts(self):
        archive, manifest, _ = self.package()
        manager = ReleaseManager(self.root)
        unrelated = [self.root / 'orphaned' / ('3.0.1-' + 'a' * 32),
                     self.root / ('extension-failed-' + 'b' * 32)]
        for path in unrelated:
            path.mkdir(parents=True)
            (path / 'sentinel').write_text('untracked')
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            for attempt in range(5):
                manager.activate(manager.stage(archive, manifest))
                manager.rollback()
                self.assertEqual(attempt + 1, read_json(self.root / 'failed.json')['attempts'])
                self.assertEqual([], read_json(self.root / 'failed-artifacts.json'))
                self.assertEqual([unrelated[0]], list((self.root / 'orphaned').iterdir()))
                self.assertEqual([unrelated[1]], list(self.root.glob('extension-failed-*')))
                self.assertTrue((self.root / 'versions/3.0.1/app' / executable_name()).is_file())
        self.assertEqual('3.0.0', manager.active()['version'])
        for path in unrelated:
            self.assertEqual('untracked', (path / 'sentinel').read_text())

    def failed_artifact(self, manager, version='3.0.1', identifier='a'):
        path = self.root / 'orphaned' / (version + '-' + identifier * 32)
        path.mkdir(parents=True)
        (path / 'sentinel').write_text('failed attempt')
        manager._remember_failed_artifact(path, version)
        return path

    def test_failed_cleanup_protects_current_last_good_previous_and_pending(self):
        manager = ReleaseManager(self.root)
        protected = [self.failed_artifact(manager, version)
                     for version in ('3.0.0', '2.9.0', '2.8.0')]
        disposable = self.failed_artifact(manager)
        atomic_json(self.root / 'last-good.json', dict(version='2.9.0', previous_version='2.8.0'))
        atomic_json(self.root / 'pending.json', dict(version='3.0.1', previous={'version': '3.0.0'}))
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager._cleanup_failed_artifacts()
            self.assertTrue(disposable.is_dir())
            (self.root / 'pending.json').unlink()
            manager._cleanup_failed_artifacts()
        self.assertFalse(disposable.exists())
        self.assertTrue(all(path.is_dir() for path in protected))
        self.assertEqual(3, len(read_json(self.root / 'failed-artifacts.json')))

    def test_busy_and_locked_failed_artifacts_are_retried_without_blocking_updates(self):
        archive, manifest, _ = self.package()
        manager = ReleaseManager(self.root)
        with patch.object(manager, '_failed_artifact_in_use', return_value=True):
            manager.activate(manager.stage(archive, manifest))
            manager.rollback()
            manager.activate(manager.stage(archive, manifest))
            manager.rollback()
        entries = read_json(self.root / 'failed-artifacts.json')
        self.assertEqual(3, len(entries))
        with patch.object(manager, '_failed_artifact_in_use', return_value=False), \
                patch('agent_tracker.core.release_manager.shutil.rmtree', side_effect=PermissionError('locked')):
            manager.activate(manager.stage(archive, manifest))
            manager.rollback()
        self.assertEqual('3.0.0', manager.active()['version'])
        self.assertEqual(5, len(read_json(self.root / 'failed-artifacts.json')))
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager._cleanup_failed_artifacts()
        self.assertEqual([], read_json(self.root / 'failed-artifacts.json'))
        self.assertEqual([], list((self.root / 'orphaned').iterdir()))
        self.assertEqual([], list(self.root.glob('extension-failed-*')))

    def test_cleanup_rejects_unrelated_paths_and_redirected_artifacts(self):
        manager = ReleaseManager(self.root)
        artifact = self.failed_artifact(manager)
        artifact.rename(self.root / 'must-retain')
        try:
            artifact.symlink_to(self.root / 'must-retain', target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks unavailable')
        entries = read_json(self.root / 'failed-artifacts.json')
        entries += [{'path': path, 'version': '3.0.1'} for path in
                    ('versions/3.0.0', '../must-retain', 'extension', 'orphaned/unrelated')]
        atomic_json(self.root / 'failed-artifacts.json', entries)
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager._cleanup_failed_artifacts()
        self.assertEqual(entries, read_json(self.root / 'failed-artifacts.json'))
        self.assertTrue(artifact.is_symlink())
        self.assertTrue((self.root / 'must-retain/sentinel').is_file())
        self.assertTrue((self.root / 'versions/3.0.0/build.json').is_file())
        self.assertTrue((self.root / 'extension/manifest.json').is_file())

    def test_cleanup_does_not_follow_redirected_orphan_parent(self):
        manager = ReleaseManager(self.root)
        artifact = self.failed_artifact(manager)
        outside = Path(self.tmp.name).resolve() / 'outside'
        artifact.parent.rename(outside)
        try:
            artifact.parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks unavailable')
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager._cleanup_failed_artifacts()
        self.assertTrue((outside / artifact.name / 'sentinel').is_file())

    def test_cleanup_preserves_artifact_referenced_by_a_protected_version_link(self):
        manager = ReleaseManager(self.root)
        artifact = self.failed_artifact(manager)
        try:
            (self.root / 'versions/2.9.0').symlink_to(artifact, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlinks unavailable')
        atomic_json(self.root / 'last-good.json', {'version': '2.9.0'})
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager._cleanup_failed_artifacts()
        self.assertTrue((artifact / 'sentinel').is_file())
        self.assertEqual(1, len(read_json(self.root / 'failed-artifacts.json')))

    def test_failed_cleanup_process_guard_checks_renamed_payload_and_command(self):
        import psutil
        manager = ReleaseManager(self.root)
        artifact = self.failed_artifact(manager)
        process = Mock(pid=123)
        process.username.return_value = 'fixture-owner'
        with patch.object(psutil, 'Process', return_value=process), \
                patch.object(psutil, 'process_iter', return_value=[process]):
            for executable in (artifact / 'app/runtime', self.root / 'versions/3.0.1/app/runtime'):
                process.exe.return_value = str(executable)
                process.cmdline.return_value = []
                self.assertTrue(manager._failed_artifact_in_use(artifact, '3.0.1'))
            process.exe.return_value = '/usr/bin/python'
            process.cmdline.return_value = ['python', str(artifact / 'worker.py')]
            self.assertTrue(manager._failed_artifact_in_use(artifact, '3.0.1'))
            process.cmdline.return_value = ['python', '/unrelated/worker.py']
            self.assertFalse(manager._failed_artifact_in_use(artifact, '3.0.1'))
            process.exe.side_effect = psutil.AccessDenied(123)
            self.assertTrue(manager._failed_artifact_in_use(artifact, '3.0.1'))

    def test_unrecorded_orphan_is_not_claimed_without_a_matching_failure(self):
        archive, manifest, _ = self.package()
        manager = ReleaseManager(self.root)
        manager.activate(manager.stage(archive, manifest))
        with patch.object(manager, '_failed_artifact_in_use', return_value=False):
            manager.rollback()
            (self.root / 'failed.json').unlink()
            manager.activate(manager.stage(archive, manifest))
            manager.rollback()
        self.assertEqual(1, len(list((self.root / 'orphaned').iterdir())))
        self.assertEqual([], read_json(self.root / 'failed-artifacts.json'))

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

    def test_removal_cancels_download_without_touching_active_release_or_queue(self):
        archive, manifest, _ = self.package()
        root = self.root
        class RemovingResponse(Response):
            def iter_content(self, size):
                atomic_json(root / 'uninstall-requested.json', {'state': 'uninstall_requested'})
                yield self.raw
        class Session:
            def get(self, url, **kwargs):
                return RemovingResponse(archive.read_bytes())
        manager = ReleaseManager(root, Session())
        with self.assertRaisesRegex(ValueError, 'cancelled for application removal'):
            manager.download(manifest)
        self.assertEqual([], list((root / 'downloads').iterdir()))
        self.assertEqual('3.0.0', manager.active()['version'])
        self.assertEqual('must survive', (root / 'outbox-sentinel').read_text())

    def installer_fixture(self, engine=None):
        archive,manifest,envelope=self.package(engine=engine)
        bundle=Path(self.tmp.name).resolve()/'setup';bundle.mkdir()
        (bundle/'release.zip').write_bytes(archive.read_bytes())
        atomic_json(bundle/'manifest.json',envelope)
        atomic_json(bundle/'trusted-update-keys.json',self.keys)
        atomic_json(bundle/'setup-build.json',manifest)
        root=Path(self.tmp.name).resolve()/'new-install'
        install(bundle,root,'a'*32,integrate=False)
        return bundle, root

    def test_macos_engine_bootstraps_install_and_repair_archives_before_wrappers(self):
        from agent_tracker import installer
        engine = b'new-layout engine fixture'
        archive_name = 'engine-' + hashlib.sha256(engine).hexdigest() + '.zip'
        replacements = []
        replace = os.replace
        def record(source, destination):
            source, destination = Path(source), Path(destination)
            if source.parent.name.startswith('.bootstrap-'):
                replacements.append(destination.name)
                if destination.name != archive_name:
                    self.assertEqual((destination.parent / archive_name).read_bytes(), engine)
            return replace(source, destination)
        with patch.object(installer, 'runtime_target', return_value=(self.old['os'], self.old['architecture'])), \
                patch.object(installer.sys, 'platform', 'darwin'), patch.object(installer.os, 'replace', side_effect=record):
            bundle, root = self.installer_fixture(engine=engine)
            wrappers = [executable_name(native, True) for native in (False, True)]
            self.assertEqual(replacements, [archive_name, *wrappers])
            for name in wrappers:
                if os.name != 'nt':
                    self.assertEqual(stat.S_IMODE((root / name).stat().st_mode), 0o700)
                else:
                    self.assertEqual((root / name).read_bytes(), (root / 'versions/3.0.1/launcher' / name).read_bytes())
            before = (root / 'current.json').read_bytes(), (root / 'enrollment.json').read_bytes()
            originals = {name: (root / name).read_bytes() for name in wrappers}
            (root / archive_name).unlink()
            for name in wrappers:
                (root / name).write_bytes(b'damaged wrapper')
            replacements.clear()
            with patch.object(installer, 'stopped_supervisor') as stop, SingleInstance(root / 'supervisor.lock'):
                install(bundle, root, 'a'*32, integrate=False)
            stop.assert_not_called()
        self.assertEqual(replacements, [archive_name, *wrappers])
        self.assertEqual(originals, {name: (root / name).read_bytes() for name in wrappers})
        self.assertEqual(before, ((root / 'current.json').read_bytes(), (root / 'enrollment.json').read_bytes()))
        self.assertEqual(list(root.glob('.bootstrap-*')), [])

    def test_macos_bootstrap_copy_failure_leaves_existing_files_and_no_temporary_files(self):
        from agent_tracker import installer
        with patch.object(installer, 'runtime_target', return_value=(self.old['os'], self.old['architecture'])), \
                patch.object(installer.sys, 'platform', 'darwin'):
            _, root = self.installer_fixture(engine=b'engine fixture')
            release = root / 'versions/3.0.1'
            before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
            copy = installer.shutil.copy2
            def fail(source, destination):
                if source.name == executable_name(True, True):
                    raise OSError('copy failed')
                return copy(source, destination)
            with patch.object(installer.shutil, 'copy2', side_effect=fail), self.assertRaisesRegex(OSError, 'copy failed'):
                installer.copy_bootstrap_files(release, root, refresh=True)
        self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()})
        self.assertEqual(list(root.glob('.bootstrap-*')), [])

    def test_macos_manual_engine_upgrade_preserves_original_bootstrap_and_sidecar(self):
        from agent_tracker import installer
        with patch.object(installer, 'runtime_target', return_value=(self.old['os'], self.old['architecture'])), \
                patch.object(installer.sys, 'platform', 'darwin'):
            bundle, root = self.installer_fixture(engine=b'first engine fixture')
            wrappers = [executable_name(native, True) for native in (False, True)]
            before = {name: (root / name).read_bytes() for name in wrappers}
            engine = b'future engine fixture'
            archive_name = 'engine-' + hashlib.sha256(engine).hexdigest() + '.zip'
            archive, _, envelope = self.package('3.0.2', engine=engine)
            (bundle / 'release.zip').write_bytes(archive.read_bytes())
            atomic_json(bundle / 'manifest.json', envelope)
            def check(root, version):
                self.assertEqual(version, '3.0.2')
                self.assertEqual(before, {name: (root / name).read_bytes() for name in wrappers})
                self.assertFalse((root / archive_name).exists())
            def fail(root, version):
                check(root, version)
                raise ValueError('health failed')
            with self.assertRaisesRegex(InstallerError, 'setup_upgrade_failed'):
                install(bundle, root, 'a'*32, integrate=False, health_check=fail)
            self.assertEqual(read_json(root / 'current.json')['version'], '3.0.1')
            self.assertEqual(before, {name: (root / name).read_bytes() for name in wrappers})
            self.assertFalse((root / archive_name).exists())
            install(bundle, root, 'a'*32, integrate=False, health_check=check)
        self.assertEqual(read_json(root / 'current.json')['version'], '3.0.2')
        self.assertFalse((root / archive_name).exists())
        self.assertEqual((root / 'versions/3.0.2/launcher' / archive_name).read_bytes(), engine)
        for name in wrappers:
            self.assertEqual((root / name).read_bytes(), before[name])

    def test_engine_sidecars_do_not_change_non_macos_bootstrap_behavior(self):
        from agent_tracker import installer
        for system in ('linux', 'win32'):
            with self.subTest(system=system), patch.object(installer.sys, 'platform', system):
                source = self.root / 'fixture' / system
                source.mkdir(parents=True)
                with patch.object(installer.shutil, 'copy2') as copy:
                    installer.copy_bootstrap_files(source, self.root)
                    self.assertEqual(copy.call_count, 2)
                    copy.reset_mock()
                    installer.copy_bootstrap_files(source, self.root, refresh=True)
                    copy.assert_not_called()

    def test_legacy_installer_main_uses_shared_setup_payload_lookup(self):
        from agent_tracker import installer
        payload = Path(self.tmp.name) / 'explicit-setup-payload'
        helper = Mock(return_value=payload)
        with patch.dict(sys.modules, {'agent_tracker.setup_payload': SimpleNamespace(setup_payload=helper)}), \
                patch.object(installer, 'bootstrap_code', return_value='a'*32), \
                patch.object(installer, 'InstallerWindow') as window:
            self.assertEqual(installer.main(), 0)
        helper.assert_called_once_with()
        window.assert_called_once_with(payload, 'a'*32, auto_migrate=True)
        window.return_value.root.mainloop.assert_called_once_with()

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
        root = Path(self.tmp.name).resolve() / 'fresh-install'
        startup = Path(self.tmp.name).resolve() / 'login' / 'soft.desktop'
        with patch('agent_tracker.installer.protect_workspace'), patch('agent_tracker.installer.register_host'), \
                patch('agent_tracker.installer.shortcuts'), patch.object(integration, '_system', return_value='linux'), \
                patch.object(integration, '_startup_path', return_value=startup):
            launcher = install(bundle, root, 'a'*32)
            self.migration.assert_called_once_with(root.resolve())
            self.registration.assert_called_once_with(root.resolve())
            self.assertTrue(integration.autostart_status(launcher)['registered'])
            self.assertIn('--autostart', startup.read_text())

    def test_same_version_setup_repairs_startup_without_stopping_or_replacing_data(self):
        from agent_tracker import integration
        bundle, root = self.installer_fixture()
        startup = Path(self.tmp.name).resolve() / 'login' / 'soft.desktop'
        before = (root/'enrollment.json').read_bytes(), (root/'current.json').read_bytes()
        with patch.object(integration, '_system', return_value='linux'), \
                patch.object(integration, '_startup_path', return_value=startup), \
                patch('agent_tracker.installer.stopped_supervisor') as stop, SingleInstance(root/'supervisor.lock'):
            launcher = install(bundle, root, 'a'*32)
            self.assertTrue(integration.autostart_status(launcher)['registered'])
            stop.assert_not_called()
        self.assertEqual(before, ((root/'enrollment.json').read_bytes(), (root/'current.json').read_bytes()))
        self.registration.assert_called_once_with(root.resolve())

    def test_optional_legacy_retirement_preserves_new_and_existing_integrations(self):
        bundle, root = self.installer_fixture()
        fresh = Path(self.tmp.name).resolve() / 'fresh-opt-out'
        with patch('agent_tracker.installer.protect_workspace'), patch('agent_tracker.installer.register_host'), \
                patch('agent_tracker.installer.shortcuts'), patch('agent_tracker.installer.autostart'):
            install(bundle, fresh, 'a'*32, retire_legacy=False)
            install(bundle, root, 'a'*32, retire_legacy=False)
        self.migration.assert_not_called()
        self.assertEqual(self.registration.call_count, 2)

    def test_confirmed_update_repairs_uninstall_registration(self):
        archive, manifest, _ = self.package()
        manager = ReleaseManager(self.root)
        manager.activate(manager.stage(archive, manifest))
        manager.confirm()
        if os.name == 'nt':
            self.update_registration.assert_called_once_with(self.root.resolve())

    def test_legacy_opt_out_in_install_race_branch(self):
        bundle, root = self.installer_fixture()
        original = Path.exists
        calls = []
        def exists(path):
            if path == root / 'current.json':
                calls.append(path)
                if len(calls) == 1:
                    return False
            return original(path)
        with patch.object(Path, 'exists', exists), patch('agent_tracker.installer.autostart'):
            install(bundle, root, 'a'*32, retire_legacy=False)
        self.migration.assert_not_called()
        self.registration.assert_called_once_with(root.resolve())

    def test_uninstall_registration_failure_does_not_rollback_confirmed_update(self):
        archive, manifest, _ = self.package()
        manager = ReleaseManager(self.root)
        manager.activate(manager.stage(archive, manifest))
        self.update_registration.side_effect = OSError('locked launcher')
        manager.confirm()
        self.assertEqual(manager.active()['version'], '3.0.1')
        self.assertFalse((self.root / 'pending.json').exists())

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
