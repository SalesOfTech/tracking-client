import copy
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import legacy_discovery as discovery


INSTALL_ID = '1b8b3e56-7d98-4e52-8721-157fb83b8588'
CONFIG = {'company_id': '32', 'base_url': 'https://tracking.example.test'}


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'Library/Application Support/SOFT/TrackingV3'
        self.install = self.root / 'install'

    def mock(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class WindowsTests(Fixture):
    def setUp(self):
        super().setUp()
        self.root = self.home / 'AppData/Local/SOFT/TrackingV3'
        self.install = self.root / 'install'
        self.exe = self.home / 'Legacy/SOFT Agent Tracking.exe'
        self.config = self.exe.with_name('agent_config.json')
        self.identifier = self.root.parent / 'Agent/install_id.txt'
        self.write(self.exe, b'fixture executable')
        self.write(self.config, json.dumps(CONFIG).encode())
        self.write(self.identifier, INSTALL_ID.encode())
        self.legacy = dict(files=[str(self.exe)], running=[str(self.exe)],
                           startup=['"' + str(self.exe) + '"', 1])
        self.metadata = dict(company_id=32, install_id=INSTALL_ID, username='employee',
                             machine='fixture', os='windows')
        self.mock(discovery, '_system', return_value='windows')
        self.mock(discovery, '_windows_snapshot_context', return_value=(self.home, 'domain\\employee', 4))
        self.context = self.mock(discovery.migrate_legacy, 'user_context',
                                 return_value=(self.root, self.home, 'domain\\employee', 4))
        self.capture = self.mock(discovery.migrate_legacy, 'snapshot_legacy', return_value=self.legacy)
        self.metadata_reader = self.mock(discovery.migrate_legacy, 'discover_legacy', return_value=self.metadata)
        self.replace = self.mock(discovery.legacy_migration, 'replace_current_user', return_value={'state': 'complete'})
        self.recovery = self.mock(discovery.migrate_legacy, 'restore_legacy')

    def test_empty_snapshot_does_not_check_elevation_or_parse_metadata(self):
        self.capture.return_value = dict(files=[], running=[], startup=None)
        self.context.side_effect = ValueError('elevated')
        self.assertIsNone(discovery.discover())
        self.context.assert_not_called()
        self.metadata_reader.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_identified_install_still_requires_strict_user_context(self):
        self.context.side_effect = ValueError('elevated secret')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, '^legacy_discovery_failed$'):
            discovery.discover()
        self.metadata_reader.assert_not_called()
        self.replace.assert_not_called()

    def test_context_changed_between_probe_and_capture_is_rejected(self):
        self.context.return_value = (self.root, self.home, 'domain\\employee', 5)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'context_changed'):
            discovery.discover()

    def test_snapshot_contract_and_metadata_delegate(self):
        snapshot = discovery.discover()
        self.assertEqual(snapshot['metadata'], self.metadata)
        self.assertEqual(snapshot['root'], str(self.root))
        self.assertEqual(snapshot['profile'], str(self.home))
        self.assertEqual((snapshot['owner'], snapshot['session']), ('domain\\employee', 4))
        self.assertEqual(snapshot['files'], [str(self.exe)])
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)
        self.metadata_reader.assert_called_once_with(self.home, self.legacy, self.root)

    def test_missing_file_is_not_absence(self):
        self.exe.unlink()
        with self.assertRaises(discovery.LegacyDiscoveryError):
            discovery.discover()

    def test_ambiguous_config_directories_fail_before_metadata(self):
        self.legacy['files'].append(str(self.home / 'another/soft_agent.exe'))
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'ambiguous'):
            discovery.discover()
        self.metadata_reader.assert_not_called()

    def test_invalid_config_and_id_are_bounded_and_fail_closed(self):
        for path, data in ((self.config, b'[]'), (self.config, b'null'),
                           (self.config, b'x' * (discovery.MAX_METADATA + 1)),
                           (self.config, json.dumps(dict(CONFIG, company_id=True)).encode()),
                           (self.config, json.dumps(dict(CONFIG, base_url='http://host')).encode()),
                           (self.identifier, b'not-a-uuid')):
            with self.subTest(path=path.name, size=len(data)):
                self.config.write_bytes(json.dumps(CONFIG).encode())
                self.identifier.write_bytes(INSTALL_ID.encode())
                path.write_bytes(data)
                with self.assertRaises(discovery.LegacyDiscoveryError):
                    discovery.discover()
        self.metadata_reader.assert_not_called()

    def test_revalidate_detects_config_change_even_if_metadata_unchanged(self):
        snapshot = discovery.discover()
        self.config.write_bytes(json.dumps(dict(CONFIG, extra='changed')).encode())
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'changed'):
            discovery.revalidate(snapshot)

    def test_revalidate_detects_startup_change(self):
        snapshot = copy.deepcopy(discovery.discover())
        self.legacy['startup'] = None
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'changed'):
            discovery.revalidate(snapshot)

    def test_absent_snapshot_cannot_retire_newly_appeared_legacy(self):
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'changed'):
            discovery.retire(None, self.install)
        self.replace.assert_not_called()

    def test_retirement_reuses_current_session_helper_without_renames(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.assertEqual(discovery.retire(snapshot, self.install), {'state': 'complete'})
        self.replace.assert_called_once_with(self.install, session_id=4, expected_files=[str(self.exe)])
        self.assertEqual(self.exe.read_bytes(), b'fixture executable')

    def test_wrong_install_root_never_retires(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'wrong_install_root'):
            discovery.retire(snapshot, self.home)
        self.replace.assert_not_called()

    def test_partial_retirement_is_compensated_and_error_is_sanitized(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.replace.side_effect = OSError('secret path and payload')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, '^legacy_retirement_failed_rolled_back$'):
            discovery.retire(snapshot, self.install)
        self.recovery.assert_called_once_with(snapshot, session_id=4)

    def test_failed_compensation_requires_recovery(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.replace.side_effect = OSError('failure')
        self.recovery.side_effect = OSError('failure')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'recovery_required'):
            discovery.retire(snapshot, self.install)

    def test_restore_refuses_a_different_rdp_session(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.context.return_value = (self.root, self.home, 'domain\\employee', 5)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'context_changed'):
            discovery.restore(snapshot, self.install)
        self.recovery.assert_not_called()

    def test_restore_before_retirement_reuses_original_snapshot(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.assertEqual(discovery.restore(snapshot, self.install), {'state': 'rolled_back'})
        self.recovery.assert_called_once_with(snapshot, session_id=4)
        self.replace.assert_not_called()
        self.assertTrue(self.exe.is_file())

    def test_restore_refuses_changed_metadata_and_binary(self):
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        self.exe.write_bytes(b'changed')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'changed'):
            discovery.restore(snapshot, self.install)
        self.recovery.assert_not_called()

    def test_empty_retirement_and_restore_do_not_create_directories(self):
        self.capture.return_value = dict(files=[], running=[], startup=None)
        self.assertIsNone(discovery.revalidate(None))
        self.assertEqual(discovery.retire(None, self.install)['state'], 'not_found')
        self.assertEqual(discovery.restore(None, self.install)['state'], 'not_found')
        self.assertFalse(self.root.exists())


class MacTests(Fixture):
    def setUp(self):
        super().setUp()
        self.bundle = self.home / 'Applications' / discovery.macos_legacy.APP
        self.exe = self.bundle / 'Contents/MacOS' / discovery.MAC_EXECUTABLE
        self.config = self.exe.with_name('agent_config.json')
        self.info = self.bundle / 'Contents/Info.plist'
        self.identifier = self.home / 'Library/Application Support/SOFT/Agent/install_id.txt'
        self.startup = self.home / 'Library/LaunchAgents/com.soft.agenttracker.plist'
        self.uid = self.home.stat().st_uid
        self.loaded = False
        self.processes = []
        self.mock(discovery, '_system', return_value='macos')
        self.mock(discovery, '_mac_account', return_value=(self.home, self.uid, 'employee'))
        # The real UID/home gate is tested separately; Windows fixtures have st_uid=0.
        self.real_context = discovery._mac_context
        self.mock(discovery, '_mac_context', return_value=self.root)
        self.mock(discovery.platform, 'node', return_value='fixture-mac')
        self.mock(discovery.psutil, 'process_iter', side_effect=lambda: list(self.processes))
        self.mock(discovery.psutil, 'Process', side_effect=self.process_for)
        self.real_launchctl = discovery._launchctl
        self.launch = self.mock(discovery, '_launchctl', side_effect=self.launchctl)
        self.spawn = self.mock(discovery.subprocess, 'Popen')
        real_exists = discovery._exists
        self.mock(discovery, '_exists', side_effect=lambda path: False if Path(path) ==
                  Path('/Applications') / discovery.macos_legacy.APP else real_exists(path))

    def process_for(self, pid):
        for process in self.processes:
            if process.pid == pid:
                return process
        raise discovery.psutil.NoSuchProcess(pid)

    def launchctl(self, command, *args):
        if command == 'print':
            return 0 if self.loaded else 113
        if command == 'bootout':
            self.loaded = False
        elif command == 'bootstrap':
            self.loaded = True
        else:
            self.fail('Unexpected launchctl command')
        return 0

    def make_bundle(self):
        self.write(self.exe, b'fixture executable')
        self.write(self.info, plistlib.dumps({'CFBundleExecutable': discovery.MAC_EXECUTABLE}))
        self.write(self.config, json.dumps(CONFIG).encode())
        self.write(self.identifier, INSTALL_ID.encode())

    def make_startup(self, **extra):
        self.write(self.startup, plistlib.dumps(dict(Label=discovery.macos_legacy.LABEL,
                   ProgramArguments=[str(self.exe)], RunAtLoad=True, KeepAlive=True, **extra)))

    def make_process(self, uid=None, exe=None, pid=77):
        process = Mock(pid=pid)
        process.name.return_value = discovery.MAC_EXECUTABLE
        process.uids.return_value = Mock(real=self.uid if uid is None else uid,
                                        effective=self.uid if uid is None else uid)
        process.exe.return_value = str(exe or self.exe)
        process.cmdline.return_value = [str(exe or self.exe)]
        process.create_time.return_value = 123.5
        process.kill.side_effect = lambda: self.processes.remove(process)
        self.processes.append(process)
        return process

    def prepared(self, *, loaded=False, running=False):
        self.make_bundle()
        self.make_startup()
        self.loaded = loaded
        if running:
            self.make_process()
        snapshot = discovery.discover()
        self.install.mkdir(parents=True, exist_ok=True)
        return snapshot

    def test_absence_is_benign_even_for_admin_and_stale_uuid(self):
        self.write(self.identifier, INSTALL_ID.encode())
        self.assertIsNone(discovery.discover())
        discovery._mac_context.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_real_layout_and_identity_match_legacy_source(self):
        self.make_bundle()
        snapshot = discovery.discover()
        self.assertEqual(snapshot['metadata'], dict(company_id=32, install_id=INSTALL_ID,
                         username='employee', machine='fixture-mac', os='macos'))
        self.assertEqual(snapshot['root'], str(self.root))
        self.assertEqual(snapshot['files'], [str(self.exe)])
        self.assertEqual(snapshot['session'], 'gui/' + str(self.uid))
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)
        self.assertNotIn(CONFIG['base_url'], json.dumps(snapshot))
        self.assertFalse(self.root.exists())

    def test_config_is_required_beside_executable_not_resources(self):
        self.make_bundle()
        resources = self.bundle / 'Contents/Resources/agent_config.json'
        resources.parent.mkdir()
        self.config.rename(resources)
        with self.assertRaises(discovery.LegacyDiscoveryError):
            discovery.discover()

    def test_app_local_uuid_is_not_used(self):
        self.make_bundle()
        self.identifier.rename(self.exe.with_name('install_id.txt'))
        with self.assertRaises(discovery.LegacyDiscoveryError):
            discovery.discover()

    def test_startup_discovers_exact_home_path_without_crawling(self):
        self.bundle = self.home / 'Desktop/Tools' / discovery.macos_legacy.APP
        self.exe = self.bundle / 'Contents/MacOS' / discovery.MAC_EXECUTABLE
        self.info = self.bundle / 'Contents/Info.plist'
        self.config = self.exe.with_name('agent_config.json')
        self.make_bundle()
        self.make_startup()
        with patch.object(Path, 'rglob', side_effect=AssertionError('No crawling')):
            self.assertEqual(discovery.discover()['bundle'], str(self.bundle))

    def test_two_home_bundles_are_ambiguous_before_any_mutation(self):
        self.make_bundle()
        (self.home / 'Downloads' / discovery.macos_legacy.APP).mkdir(parents=True)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'ambiguous'):
            discovery.discover()
        self.assertTrue(self.bundle.exists())
        self.launch.assert_not_called()

    def test_shared_app_requires_review_and_is_never_adopted(self):
        exists = discovery._exists.side_effect
        discovery._exists.side_effect = lambda path: True if Path(path) == (
            Path('/Applications') / discovery.macos_legacy.APP) else exists(path)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'shared_install'):
            discovery.discover()
        self.launch.assert_not_called()

    def test_foreign_process_is_not_inspected_or_stopped(self):
        process = self.make_process(uid=self.uid + 1)
        self.assertIsNone(discovery.discover())
        process.exe.assert_not_called()
        process.kill.assert_not_called()

    def test_current_process_outside_home_is_not_adopted(self):
        process = self.make_process(exe=self.home.parent / 'other' / discovery.macos_legacy.APP /
                                    'Contents/MacOS' / discovery.MAC_EXECUTABLE)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'outside_current_home'):
            discovery.discover()
        process.kill.assert_not_called()

    def test_unknown_owner_of_identified_process_blocks(self):
        process = self.make_process()
        process.uids.side_effect = discovery.psutil.AccessDenied(process.pid)
        with self.assertRaises(discovery.LegacyDiscoveryError):
            discovery.discover()
        process.kill.assert_not_called()

    def test_custom_process_arguments_block(self):
        self.make_bundle()
        process = self.make_process()
        process.cmdline.return_value += ['--config', 'custom.json']
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'custom_launch'):
            discovery.discover()
        process.kill.assert_not_called()

    def test_custom_startup_environment_is_not_trusted(self):
        self.make_bundle()
        self.make_startup(EnvironmentVariables={'USER': 'someone-else'})
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'unrecognized_startup'):
            discovery.discover()
        self.launch.assert_not_called()

    def test_malformed_and_oversized_metadata_are_not_absence(self):
        self.make_bundle()
        self.make_startup()
        for path in (self.config, self.info, self.startup, self.identifier):
            original = path.read_bytes()
            for data in (b'not valid metadata', b'x' * (discovery.MAX_METADATA + 1)):
                with self.subTest(path=path.name, size=len(data)):
                    path.write_bytes(data)
                    with self.assertRaises(discovery.LegacyDiscoveryError):
                        discovery.discover()
            path.write_bytes(original)
        self.assertTrue(self.bundle.is_dir())

    def test_invalid_company_and_endpoint_are_not_exposed(self):
        self.make_bundle()
        for config in ([], {}, dict(CONFIG, company_id=False), dict(CONFIG, company_id='01'),
                       dict(CONFIG, base_url='https://private:secret@host')):
            self.config.write_text(json.dumps(config), encoding='utf-8')
            with self.assertRaises(discovery.LegacyDiscoveryError) as caught:
                discovery.discover()
            self.assertNotIn('secret', str(caught.exception))

    def test_ancestor_link_and_foreign_ownership_are_rejected(self):
        self.make_bundle()
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'ownership_or_link'):
            discovery._owned_path(self.exe, self.home, self.uid + 1)
        original = Path.lstat
        with patch.object(Path, 'lstat', autospec=True, side_effect=lambda path: Mock(
                st_mode=stat.S_IFLNK, st_uid=self.uid, st_file_attributes=0) if path == self.bundle
                else original(path)):
            with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'ownership_or_link'):
                discovery.discover()

    def test_real_context_rejects_root_and_inherited_username(self):
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'conflicting_user_context'):
            self.real_context(self.home, 0, 'employee')
        with patch.object(discovery.os, 'geteuid', return_value=501, create=True), \
                patch.object(Path, 'home', return_value=self.home), \
                patch.object(discovery.getpass, 'getuser', return_value='other'), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'conflicting_user_context'):
                self.real_context(self.home, 501, 'employee')

    def test_launchd_failure_is_not_reported_as_absence(self):
        self.make_bundle()
        self.launch.side_effect = lambda *args: 5
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'launchd_inspection_failed'):
            discovery.discover()

    def test_loaded_job_without_restorable_plist_blocks(self):
        self.make_bundle()
        self.loaded = True
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'launchd_without_startup'):
            discovery.discover()

    def test_changed_snapshot_blocks_before_retirement(self):
        snapshot = self.prepared()
        self.config.write_bytes(json.dumps(dict(CONFIG, company_id=33)).encode())
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'legacy_changed'):
            discovery.retire(snapshot, self.install)
        self.assertTrue(self.bundle.is_dir())
        self.assertTrue(self.startup.is_file())
        self.assertFalse((self.install / discovery.JOURNAL).exists())

    def test_successful_retirement_and_restore_preserve_exact_files(self):
        snapshot = self.prepared(loaded=True, running=True)
        process = self.processes[0]
        startup_bytes = self.startup.read_bytes()
        report = discovery.retire(snapshot, self.install)
        self.assertEqual(report['state'], 'complete')
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.startup.exists())
        self.assertFalse(self.loaded)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        self.assertEqual(self.identifier.read_text(), INSTALL_ID)
        backup = self.install / report['backup'] / discovery.macos_legacy.APP
        self.assertTrue(backup.is_dir())
        self.assertEqual(discovery.restore(json.loads(json.dumps(snapshot)), self.install)['state'], 'rolled_back')
        self.assertTrue(self.bundle.is_dir())
        self.assertEqual(self.startup.read_bytes(), startup_bytes)
        self.assertTrue(self.loaded)
        self.assertFalse(backup.exists())
        self.spawn.assert_not_called()

    def test_partial_rename_failure_restores_startup_and_loaded_job(self):
        snapshot = self.prepared(loaded=True)
        original = Path.rename
        def rename(path, destination):
            if path == self.bundle:
                raise OSError('fixture failure')
            return original(path, destination)
        with patch.object(Path, 'rename', autospec=True, side_effect=rename):
            with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'failed_rolled_back'):
                discovery.retire(snapshot, self.install)
        self.assertTrue(self.bundle.is_dir())
        self.assertTrue(self.startup.is_file())
        self.assertTrue(self.loaded)
        self.assertEqual(json.loads((self.install / discovery.JOURNAL).read_text())['state'], 'rolled_back')

    def test_failure_unloading_service_leaves_files_and_process_untouched(self):
        snapshot = self.prepared(loaded=True, running=True)
        process = self.processes[0]
        self.launch.side_effect = lambda command, *args: 5 if command == 'bootout' else self.launchctl(command, *args)
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'failed_rolled_back'):
            discovery.retire(snapshot, self.install)
        self.assertTrue(self.bundle.is_dir())
        self.assertTrue(self.startup.is_file())
        process.kill.assert_not_called()

    def test_recycled_pid_is_never_killed(self):
        snapshot = self.prepared(running=True)
        replacement = Mock()
        replacement.create_time.return_value = 999
        discovery.psutil.Process.side_effect = lambda pid: replacement
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'failed_rolled_back'):
            discovery.retire(snapshot, self.install)
        replacement.kill.assert_not_called()
        self.assertTrue(self.bundle.is_dir())

    def test_process_only_install_is_restarted_on_restore(self):
        self.make_bundle()
        self.make_process()
        snapshot = discovery.discover()
        self.install.mkdir(parents=True)
        discovery.retire(snapshot, self.install)
        discovery.restore(snapshot, self.install)
        self.spawn.assert_called_once_with([str(self.exe)], cwd=str(self.exe.parent))

    def test_restore_checks_all_destinations_before_moving_anything(self):
        snapshot = self.prepared()
        report = discovery.retire(snapshot, self.install)
        self.startup.write_bytes(b'new unrelated startup')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'rollback_collision'):
            discovery.restore(snapshot, self.install)
        self.assertFalse(self.bundle.exists())
        self.assertTrue((self.install / report['backup'] / discovery.macos_legacy.APP).exists())
        self.assertEqual(self.startup.read_bytes(), b'new unrelated startup')

    def test_restore_rejects_modified_backup(self):
        snapshot = self.prepared()
        report = discovery.retire(snapshot, self.install)
        backup_config = self.install / report['backup'] / discovery.macos_legacy.APP / 'Contents/MacOS/agent_config.json'
        backup_config.write_bytes(b'{}')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'changed'):
            discovery.restore(snapshot, self.install)
        self.assertFalse(self.bundle.exists())
        self.assertFalse(self.startup.exists())

    def test_restore_rejects_a_tampered_journal(self):
        snapshot = self.prepared()
        discovery.retire(snapshot, self.install)
        path = self.install / discovery.JOURNAL
        report = json.loads(path.read_text())
        report['backup'] = '../other-user'
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'invalid_rollback_journal'):
            discovery.restore(snapshot, self.install)
        self.assertFalse(self.bundle.exists())

    def test_restore_never_crosses_uid_boundary(self):
        snapshot = self.prepared()
        discovery.retire(snapshot, self.install)
        discovery._mac_account.return_value = (self.home, self.uid + 1, 'other')
        with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'context_changed'):
            discovery.restore(snapshot, self.install)
        self.assertFalse(self.bundle.exists())

    def test_restore_is_idempotent_for_loaded_job(self):
        snapshot = self.prepared(loaded=True)
        discovery.retire(snapshot, self.install)
        discovery.restore(snapshot, self.install)
        count = len(self.launch.call_args_list)
        discovery.restore(snapshot, self.install)
        self.assertTrue(self.bundle.exists())
        self.assertEqual(len(self.launch.call_args_list), count + 1)

    def test_restore_before_retirement_does_not_touch_legacy(self):
        snapshot = self.prepared(loaded=True, running=True)
        self.assertEqual(discovery.restore(snapshot, self.install), {'state': 'not_retired'})
        self.processes[0].kill.assert_not_called()
        self.assertTrue(self.bundle.is_dir())
        self.assertTrue(self.startup.is_file())
        self.assertTrue(self.loaded)

    def test_crash_recovery_infers_partial_moves_from_prepared_report(self):
        for moved in (0, 1, 2):
            with self.subTest(moved=moved):
                snapshot = self.prepared(loaded=True)
                report = dict(binding=discovery._binding(snapshot),
                              backup='legacy-retirement-' + str(moved) * 32, state='prepared')
                backup = self.install / report['backup']
                backup.mkdir()
                discovery.atomic_json(self.install / discovery.JOURNAL, report)
                self.loaded = False
                moves = list(reversed(discovery._moves(snapshot, self.install, report)))
                for source, destination in moves[:moved]:
                    source.rename(destination)
                self.assertEqual(discovery.restore(snapshot, self.install)['state'], 'rolled_back')
                self.assertTrue(self.bundle.is_dir())
                self.assertTrue(self.startup.is_file())
                self.assertTrue(self.loaded)

    def test_rediscovery_with_new_pid_can_retire_after_successful_rollback(self):
        snapshot = self.prepared(running=True)
        discovery.retire(snapshot, self.install)
        discovery.restore(snapshot, self.install)
        replacement = self.make_process(pid=88)
        replacement.create_time.return_value = 456.5
        fresh = discovery.discover()
        self.assertNotEqual(fresh['processes'], snapshot['processes'])
        self.assertEqual(discovery.retire(fresh, self.install)['state'], 'complete')
        replacement.kill.assert_called_once()

    def test_subprocess_timeout_and_output_suppression(self):
        with patch.object(discovery.subprocess, 'run', return_value=Mock(returncode=113)) as run:
            self.assertEqual(self.real_launchctl('print', 'gui/501/com.soft.agenttracker'), 113)
        self.assertEqual(run.call_args.kwargs['timeout'], 15)
        self.assertEqual(run.call_args.kwargs['stdout'], discovery.subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['stderr'], discovery.subprocess.DEVNULL)


class LinuxTests(Fixture):
    def setUp(self):
        super().setUp()
        self.mock(discovery, '_system', return_value='linux')
        self.mock(discovery.os, 'getuid', return_value=1001, create=True)
        self.process = Mock(pid=77)
        self.process.uids.return_value = Mock(real=1001)
        self.process.name.return_value = 'SOFT Tracking'
        self.mock(discovery.psutil, 'process_iter', return_value=[self.process])
        self.inspect = self.mock(discovery, '_inspect', side_effect=AssertionError('No invented Linux paths'))

    def test_v3_is_not_historical_legacy(self):
        self.assertIsNone(discovery.discover())
        self.inspect.assert_not_called()
        self.process.uids.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_known_legacy_process_is_unsupported_not_removed(self):
        for name in ('SOFT Agent Tracking', 'soft_agent', 'soft_agent_windows.exe'):
            self.process.name.return_value = name
            with self.assertRaisesRegex(discovery.LegacyDiscoveryError, 'linux_install_unsupported'):
                discovery.discover()
        self.process.kill.assert_not_called()
        self.process.exe.assert_not_called()
        self.inspect.assert_not_called()

    def test_foreign_legacy_process_is_ignored(self):
        self.process.name.return_value = 'SOFT Agent Tracking'
        self.process.uids.return_value = Mock(real=1002)
        self.assertIsNone(discovery.discover())
        self.process.exe.assert_not_called()
        self.process.kill.assert_not_called()

    def test_unknown_owner_is_not_absence(self):
        self.process.name.return_value = 'soft_agent'
        self.process.uids.side_effect = discovery.psutil.AccessDenied(77)
        with self.assertRaises(discovery.LegacyDiscoveryError):
            discovery.discover()

    def test_no_legacy_is_benign_for_root(self):
        discovery.os.getuid.return_value = 0
        self.assertIsNone(discovery.discover())
        self.assertEqual(discovery.retire(None, self.install), {'state': 'not_found'})


if __name__ == '__main__':
    unittest.main()
