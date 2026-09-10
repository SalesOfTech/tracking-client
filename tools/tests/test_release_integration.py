"""Build-contract tests use fixtures/mocks only; they never create native binaries."""
import hashlib
import json
import os
import stat
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'tools'))
from tools import ci, release
from smoke_helpers import assert_ui_health, assert_ui_payload
from agent_tracker.electron_links import MAP_NAME, restore_links
from agent_tracker.core.signed_updates import stage_archive


class ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def fixture(self, path, value=b'fixture'):
        path = self.root/path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def packaged(self, system='windows', arch='x64'):
        self.fixture('desktop/package.json', json.dumps({'devDependencies': {'electron': release.ELECTRON_VERSION}}).encode())
        self.fixture('desktop/package-lock.json', json.dumps({'packages': {'node_modules/electron': {'version': release.ELECTRON_VERSION}}}).encode())
        directory = Path('desktop/out')/('SoftTrackingUI-'+release.ELECTRON_PLATFORMS[system]+'-'+arch)
        self.fixture(directory/release.electron_executable(system))
        resources = directory/('SoftTrackingUI.app/Contents/Resources' if system == 'macos' else 'resources')
        self.fixture(resources/'app.asar')
        self.fixture(directory/'LICENSE')
        return self.root/directory

    def test_eight_targets_keep_six_electron_and_two_legacy_qt(self):
        self.assertEqual(len(ci.TARGETS), 8)
        self.assertEqual(sum(row['ui']=='electron' for row in ci.TARGETS), 6)
        for row in ci.TARGETS:
            with self.subTest(target=row['target']), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(release.ui_for_profile(*row['target'].split('-')), row['ui'])
                self.assertNotEqual(row['runner'], 'self-hosted')
                if row['ui']=='electron':
                    self.assertIn(row['arch'], ('arm64','x64'))

    def test_native_build_refuses_local_and_self_hosted(self):
        for env in ({}, {'GITHUB_ACTIONS':'true'}, {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'self-hosted'}):
            with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                release.build(None)
        with patch.dict(os.environ, {'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}, clear=True):
            release.require_github_runner()

    def local_args(self, **changes):
        return SimpleNamespace(**dict(dict(local_test=True, profile='modern', version='3.2.0-rc.1'), **changes))

    def test_local_build_policy_is_explicit_scoped_and_does_not_spoof_github(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(release.platform, 'system', return_value='Windows'), \
                patch.object(release.platform, 'machine', return_value='AMD64'), \
                patch('agent_tracker.core.target.runtime_target', return_value=('windows', 'x64')), \
                patch.object(release.sys, 'maxsize', 2**63 - 1):
            plan = release.build_plan(self.local_args())
            self.assertEqual(plan['output'], ROOT/'artifacts/local-test/windows-x64-modern/3.2.0-rc.1')
            self.assertEqual(plan['metadata'], {'build_channel': 'local-test'})
            self.assertEqual(plan['ui'], 'electron')
            self.assertEqual(plan['target'], 'windows-x64-modern')
            self.assertNotIn('GITHUB_ACTIONS', os.environ)
            for flag in (False, None, 'true', 1):
                with self.subTest(flag=flag), self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                    release.build_plan(self.local_args(local_test=flag))
            with self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                release.build_plan(SimpleNamespace(profile='modern', version='3.2.0-rc.1'))

    def test_local_build_rejects_other_hosts_profiles_and_python_architectures_before_key_access(self):
        cases = [('Linux', 'x86_64', ('linux', 'x64'), 'modern', 2**63 - 1),
                 ('Darwin', 'x86_64', ('macos', 'x64'), 'modern', 2**63 - 1),
                 ('Windows', 'ARM64', ('windows', 'x64'), 'modern', 2**63 - 1),
                 ('Windows', 'AMD64', ('windows', 'arm64'), 'modern', 2**63 - 1),
                 ('Windows', 'AMD64', ('windows', 'x86'), 'modern', 2**31 - 1),
                 ('Windows', 'AMD64', ('windows', 'x64'), 'legacy', 2**63 - 1),
                 ('Windows', 'unknown', ('windows', 'x64'), 'modern', 2**63 - 1)]
        for system, machine, target, profile, maxsize in cases:
            with self.subTest(system=system, machine=machine, target=target, profile=profile), \
                    patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}, clear=True), \
                    patch.object(release.platform, 'system', return_value=system), \
                    patch.object(release.platform, 'machine', return_value=machine), \
                    patch('agent_tracker.core.target.runtime_target', return_value=target), \
                    patch.object(release.sys, 'maxsize', maxsize), \
                    patch.object(Path, 'read_bytes', side_effect=AssertionError('Must not read signing keys')), \
                    self.assertRaisesRegex(ValueError, 'native Windows x64'):
                release.build(self.local_args(profile=profile))

    def test_local_build_requires_canonical_bounded_prerelease(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(release.platform, 'system', return_value='Windows'), \
                patch.object(release.platform, 'machine', return_value='AMD64'), \
                patch('agent_tracker.core.target.runtime_target', return_value=('windows', 'x64')), \
                patch.object(release.sys, 'maxsize', 2**63 - 1):
            for version in ('3.2.0', '3.02.0-rc.1', '3.2.0-rc.10000', '65536.2.0-rc.1',
                            '../3.2.0-rc.1', '3.2.0-rc.1+local', ''):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    release.build_plan(self.local_args(version=version))
            for machine in ('AMD64', 'x64', 'x86_64'):
                for version in ('3.2.0-alpha.0', '3.2.0-beta.2', '3.2.0-rc.1'):
                    with patch.object(release.platform, 'machine', return_value=machine):
                        self.assertTrue(release.build_plan(self.local_args(version=version))['local_test'])

    def test_normal_build_plan_preserves_hosted_guard_and_candidate_layout(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}, clear=True), \
                patch('agent_tracker.core.target.runtime_target', return_value=('windows', 'x64')):
            plan = release.build_plan(SimpleNamespace(profile='modern', version='3.2.0'))
            self.assertEqual(plan['output'], ROOT/'artifacts/windows-x64-modern/3.2.0')
            self.assertEqual(plan['metadata'], {})
            self.assertFalse(plan['local_test'])

    def test_local_build_requests_only_isolated_electron_package_before_key_access(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(release.platform, 'system', return_value='Windows'), \
                patch.object(release.platform, 'machine', return_value='AMD64'), \
                patch('agent_tracker.core.target.runtime_target', return_value=('windows', 'x64')), \
                patch.object(release.sys, 'maxsize', 2**63 - 1), \
                patch.object(release, 'electron_package', side_effect=RuntimeError('Fixture stop')) as package, \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('Must not read signing keys')):
            with self.assertRaisesRegex(RuntimeError, 'Fixture stop'):
                release.build(self.local_args())
            package.assert_called_once_with('windows', 'x64', local_test=True)

    def test_local_electron_package_cannot_fall_back_to_publish_candidate(self):
        normal = self.packaged()
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'Missing native'):
            release.electron_package('windows', 'x64', local_test=True)
        local = normal.parent/'local-test'/normal.name
        local.parent.mkdir()
        normal.rename(local)
        with patch.object(release, 'ROOT', self.root):
            self.assertEqual(release.electron_package('windows', 'x64', local_test=True), local)
            with self.assertRaisesRegex(ValueError, 'Missing native'):
                release.electron_package('windows', 'x64')

    def test_wrong_profile_and_ui_fail_closed(self):
        with patch.dict(os.environ, {'SOFT_TRACKING_UI':'qt'}), self.assertRaises(ValueError):
            release.ui_for_profile('windows', 'x64', 'modern')
        for args in (('windows','x86','modern'), ('macos','x64','legacy'), ('linux','armv7l','modern')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                release.ui_for_profile(*args)

    def test_electron_freeze_excludes_qt_and_keeps_native_backend(self):
        for entry in ('runtime_entry.py','setup_entry.py','native_entry.py','bootstrap_entry.py'):
            with self.subTest(entry=entry), patch.object(release.platform, 'system', return_value='Windows'), patch.object(release.subprocess, 'run') as run:
                release.freeze(entry, 'fixture', self.root/'dist', self.root/'work', ui='electron')
            args = run.call_args.args[0]
            excludes = [args[i+1] for i,item in enumerate(args) if item=='--exclude-module']
            imports = [args[i+1] for i,item in enumerate(args) if item=='--hidden-import']
            self.assertIn('PySide2', excludes)
            self.assertIn('PySide6', excludes)
            self.assertIn('tkinter', excludes)
            self.assertIn('_tkinter', excludes)
            self.assertIn('_cffi_backend', imports)
            self.assertNotIn('--additional-hooks-dir', args)
            self.assertEqual('--onedir' in args, entry=='runtime_entry.py')
            self.assertEqual('--onefile' in args, entry!='runtime_entry.py')

    def test_legacy_runtime_keeps_qt_hooks_and_qml(self):
        with patch.object(release.platform, 'system', return_value='Windows'), patch.object(release.subprocess, 'run') as run:
            release.freeze('runtime_entry.py', 'fixture', self.root/'dist', self.root/'work', ui='qt')
        args = run.call_args.args[0]
        self.assertIn('--additional-hooks-dir', args)
        self.assertTrue(any('.QtQuickControls2' in arg for arg in args))
        self.assertTrue(any('agent_tracker/ui/quick' in arg for arg in args))

    def test_native_host_stays_qt_free_even_in_legacy_profile(self):
        with patch.object(release.platform, 'system', return_value='Windows'), patch.object(release.subprocess, 'run') as run:
            release.freeze('native_entry.py', 'fixture', self.root/'dist', self.root/'work', ui='qt', console=True)
        self.assertIn('PySide2', run.call_args.args[0])
        self.assertNotIn('--windowed', run.call_args.args[0])

    def test_electron_package_paths_match_all_native_targets(self):
        for system in ('windows','macos','linux'):
            for arch in ('x64','arm64'):
                with self.subTest(system=system,arch=arch):
                    expected = self.packaged(system,arch)
                    with patch.object(release, 'ROOT', self.root):
                        self.assertEqual(release.electron_package(system,arch), expected)

    def test_electron_version_must_be_exact_in_manifest_and_lock(self):
        self.packaged()
        self.fixture('desktop/package.json', b'{"devDependencies":{"electron":"^44.3.0"}}')
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'pin electron'):
            release.electron_package('windows','x64')
        self.packaged()
        self.fixture('desktop/package-lock.json', b'{"packages":{"node_modules/electron":{"version":"43.0.0"}}}')
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'lockfile'):
            release.electron_package('windows','x64')

    def test_missing_package_is_not_replaced_with_another_architecture(self):
        self.packaged()
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'Missing native'):
            release.electron_package('windows','arm64')

    def test_package_requires_asar_and_no_development_app(self):
        source = self.packaged()
        (source/'resources/app.asar').unlink()
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'app.asar'):
            release.electron_package('windows','x64')
        self.packaged()
        (source/'resources/app').mkdir()
        with patch.object(release, 'ROOT', self.root), self.assertRaisesRegex(ValueError, 'development app'):
            release.electron_package('windows','x64')

    def test_complete_package_is_copied(self):
        source = self.packaged()
        (source/'locales').mkdir()
        (source/'locales/cs.pak').write_bytes(b'czech')
        destination = self.root/'payload/app/electron'
        release.copy_electron(source,destination)
        self.assertEqual((destination/'locales/cs.pak').read_bytes(), b'czech')
        self.assertTrue((destination/'LICENSE').is_file())
        self.assertTrue((destination/'resources/app.asar').is_file())

    def test_development_files_fail_instead_of_being_silently_dropped(self):
        source = self.packaged()
        (source/'node_modules').mkdir()
        with self.assertRaisesRegex(ValueError, 'Development files'):
            release.copy_electron(source,self.root/'payload')
        self.assertFalse((self.root/'payload').exists())

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows account lacks symlink privilege; POSIX CI must execute this test')
            raise

    def framework(self):
        source = self.packaged('macos')
        framework = source/'SoftTrackingUI.app/Contents/Frameworks/Example.framework'
        (framework/'Versions/A/Resources').mkdir(parents=True)
        (framework/'Versions/A/Example').write_bytes(b'binary' * 10000)
        (framework/'Versions/A/Example').chmod(0o755)
        (framework/'Versions/A/Resources/info').write_bytes(b'resource')
        self.symlink(framework/'Versions/Current', 'A', directory=True)
        self.symlink(framework/'Example', 'Versions/Current/Example')
        self.symlink(framework/'Resources', 'Versions/Current/Resources', directory=True)
        return source, framework.relative_to(source)

    def test_internal_framework_aliases_become_regular_map_without_duplicate_files(self):
        source, framework = self.framework()
        destination = self.root/'flat'
        release.copy_electron(source, destination)
        self.assertFalse(os.path.lexists(destination/framework/'Example'))
        self.assertFalse(os.path.lexists(destination/framework/'Versions/Current'))
        self.assertTrue((destination/framework/'Versions/A/Example').is_file())
        document = json.loads((destination/MAP_NAME).read_text())
        self.assertEqual(len(document['links']), 3)
        self.assertTrue(all(not item.is_symlink() for item in destination.rglob('*')))
        restore_links(destination)
        self.assertEqual((destination/framework/'Example').read_bytes(), b'binary' * 10000)
        self.assertEqual(os.readlink(destination/framework/'Versions/Current'), 'A')

    def test_external_or_cyclic_symlink_is_rejected(self):
        source = self.packaged('macos')
        outside = self.fixture('secret', b'private')
        link = source/'SoftTrackingUI.app/Contents/outside'
        self.symlink(link, outside)
        with self.assertRaises(ValueError):
            release.copy_electron(source,self.root/'flat')
        self.assertFalse((self.root/'flat').exists())
        link.unlink()
        self.symlink(link, '.', directory=True)
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            release.copy_electron(source,self.root/'flat')
        self.assertFalse((self.root/'flat').exists())

    def test_map_is_reserved_and_non_mac_bundle_aliases_are_rejected(self):
        source = self.packaged()
        (source/MAP_NAME).write_text('{}')
        with self.assertRaises(ValueError):
            release.copy_electron(source, self.root/'flat')
        (source/MAP_NAME).unlink()
        self.symlink(source/'alias', 'LICENSE')
        with self.assertRaisesRegex(ValueError, 'application bundle'):
            release.copy_electron(source, self.root/'flat')

    def test_empty_linked_directory_is_rejected_before_signing_unrestorable_payload(self):
        source = self.packaged('macos')
        contents = source/'SoftTrackingUI.app/Contents'
        (contents/'empty').mkdir()
        self.symlink(contents/'alias', 'empty', directory=True)
        with self.assertRaisesRegex(ValueError, 'absent from the regular-file payload'):
            release.copy_electron(source, self.root/'flat')
        self.assertFalse((self.root/'flat').exists())

    def test_deduplicated_regular_zip_stages_under_unchanged_extractor_bounds_then_restores(self):
        source, framework = self.framework()
        payload = self.root/'payload'
        release.copy_electron(source, payload/'app/electron')
        archive = self.root/'release.zip'
        release.write_payload_archive(payload, archive)
        self.assertEqual(release.TRANSITION_ARCHIVE_BYTES, 250 * 1024 * 1024)
        self.assertEqual(release.TRANSITION_UNPACKED_BYTES, 750 * 1024 * 1024)
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            self.assertTrue(all(stat.S_ISREG(item.external_attr >> 16) for item in entries))
            self.assertEqual(sum(item.filename.endswith('/Example') for item in entries), 1)
            self.assertLess(sum(item.file_size for item in entries), 70000)
        manifest = {'size': archive.stat().st_size, 'sha256': release.file_digest(archive)}
        staged = self.root/'staged'
        with patch('agent_tracker.core.signed_updates.runtime_compatible', return_value=True):
            stage_archive(archive, manifest, staged)
        electron = staged/'app/electron'
        self.assertTrue(all(not item.is_symlink() for item in staged.rglob('*')))
        restore_links(electron)
        self.assertEqual((electron/framework/'Resources/info').read_bytes(), b'resource')
        self.assertEqual((electron/framework/'Example').read_bytes(), b'binary' * 10000)
        if os.name != 'nt':
            self.assertTrue((electron/framework/'Example').stat().st_mode & 0o111)
        # An already restored tree must never sneak symlinks into the signed ZIP.
        with self.assertRaisesRegex(ValueError, 'only regular files'):
            release.write_payload_archive(staged, self.root/'unsafe.zip')
        self.assertFalse((self.root/'unsafe.zip').exists())

    def test_regular_link_map_archive_stages_without_symlink_privileges(self):
        framework = 'app/electron/SoftTrackingUI.app/Contents/Frameworks/Example.framework'
        self.fixture('payload/' + framework + '/Versions/A/Example', b'binary' * 10000)
        document = {'version': 1, 'links': [
            {'path': framework[len('app/electron/'):] + '/Example', 'target': 'Versions/A/Example'},
            {'path': framework[len('app/electron/'):] + '/Versions/Current', 'target': 'A'},
        ]}
        self.fixture('payload/app/electron/' + MAP_NAME, json.dumps(document).encode())
        archive = self.root/'regular.zip'
        release.write_payload_archive(self.root/'payload', archive)
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(len(package.infolist()), 2)
            self.assertTrue(all(stat.S_ISREG(item.external_attr >> 16) for item in package.infolist()))
            self.assertLess(sum(item.file_size for item in package.infolist()), 61000)
        with patch('agent_tracker.core.signed_updates.runtime_compatible', return_value=True):
            stage_archive(archive, {'size': archive.stat().st_size, 'sha256': release.file_digest(archive)},
                          self.root/'staged')
        self.assertEqual((self.root/'staged'/framework/'Versions/A/Example').read_bytes(), b'binary' * 10000)
        self.assertEqual(len(release.plan_links(self.root/'staged/app/electron', document)), 2)
        self.assertFalse(os.path.lexists(self.root/'staged'/framework/'Versions/Current'))

    def test_archive_rejects_directory_and_dangling_symlinks_before_opening_zip(self):
        file = self.fixture('payload/original/file', b'keep')
        alias = file.parent.parent/'alias'
        for target, directory in (('original', True), ('missing', False)):
            self.symlink(alias, target, directory=directory)
            with self.assertRaises(ValueError):
                release.write_payload_archive(file.parent.parent, self.root/'bad.zip')
            self.assertFalse((self.root/'bad.zip').exists())
            alias.unlink()

    def test_archive_preserves_layout_and_has_old_client_compatible_entries(self):
        file = self.fixture('payload/app/electron/SoftTrackingUI', b'fixture')
        file.chmod(0o755)
        archive = self.root/'release.zip'
        release.write_payload_archive(self.root/'payload',archive)
        with zipfile.ZipFile(archive) as package:
            info = package.getinfo('app/electron/SoftTrackingUI')
            self.assertEqual(package.read(info),b'fixture')
            if os.name!='nt':
                self.assertTrue((info.external_attr>>16)&0o111)

    def test_old_updater_size_limits_are_checked_before_signing(self):
        self.fixture('payload/file', b'x'*100)
        with patch.object(release, 'TRANSITION_UNPACKED_BYTES', 10), self.assertRaisesRegex(ValueError, 'extraction limit'):
            release.write_payload_archive(self.root/'payload', self.root/'bad.zip')
        self.assertFalse((self.root/'bad.zip').exists())
        with patch.object(release, 'TRANSITION_ARCHIVE_BYTES', 10), self.assertRaisesRegex(ValueError, 'download limit'):
            release.write_payload_archive(self.root/'payload', self.root/'large.zip')

    def test_streaming_digest_matches_sha256(self):
        path = self.fixture('large', b'chunk'*300000)
        self.assertEqual(release.file_digest(path),hashlib.sha256(path.read_bytes()).hexdigest())

    def test_required_upgrade_baselines_cannot_be_overridden(self):
        for version in ('3.2.0','3.2.0-rc.1','3.2.1'):
            self.assertEqual(ci.upgrade_baselines(version), ['3.1.0','3.0.4'])
            self.assertEqual(ci.upgrade_baselines(version,'3.0.0-beta.7'), ['3.1.0','3.0.4','3.0.0-beta.7'])
        self.assertEqual(ci.upgrade_baselines('3.2.0','3.1.0,3.0.4'), ['3.1.0','3.0.4'])
        self.assertEqual(len(ci.upgrade_baselines('3.2.0','3.0.0,3.0.1,3.0.2')),5)

    def test_old_release_baselines_are_preserved(self):
        self.assertEqual(ci.upgrade_baselines('3.0.0'), ['3.0.0-beta.7','3.0.0-beta.9'])
        self.assertEqual(ci.upgrade_baselines('3.0.3'), ['3.0.2','3.0.0-beta.7'])

    def test_invalid_baselines_are_rejected(self):
        for value in ('3.2.0','3.3.0','oops','3.0.4,3.0.4','3.0.0,3.0.1,3.0.2,3.0.3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ci.upgrade_baselines('3.2.0',value)

    def test_baseline_pins_cover_both_transition_versions_and_all_targets(self):
        pins = json.loads((ROOT/'tools/upgrade-baselines.json').read_text())
        for version in ('3.0.4','3.1.0','3.0.0-beta.7','3.0.0-beta.9'):
            self.assertEqual(set(pins[version]), {row['target'] for row in ci.TARGETS})
            for identifier,digest in pins[version].values():
                self.assertIsInstance(identifier,int)
                self.assertRegex(digest,r'^[a-f0-9]{64}$')

    def test_plan_exports_mandatory_upgrade_list(self):
        output = self.root/'output'
        with patch.dict(os.environ, {'GITHUB_OUTPUT':str(output),'RELEASE_TARGET':'all','RELEASE_VERSION':'3.2.0','RELEASE_UPGRADE_FROM':''}):
            ci.plan()
        result = dict(line.split('=',1) for line in output.read_text().splitlines())
        self.assertEqual(result['upgrade_from'],'3.1.0,3.0.4')
        self.assertEqual(len(json.loads(result['matrix'])['include']),8)

    def test_metadata_declares_compatibility_gate(self):
        self.assertEqual(release.ui_compatibility('windows','electron')['minimum_os_version'],'10.0.18362')
        self.assertEqual(release.ui_compatibility('macos','electron')['minimum_os_version'],'13.0')
        self.assertEqual(release.ui_compatibility('linux','electron')['minimum_glibc'],'2.35')
        self.assertTrue(release.ui_compatibility('linux','electron')['requires_compatibility_gate'])
        self.assertEqual(release.ui_compatibility('windows','qt')['profile'],'legacy')

    def test_health_must_prove_electron_renderer_not_just_python(self):
        health = self.fixture('health.json', b'{"ready":true}')
        with self.assertRaises(AssertionError):
            assert_ui_health(health,{'ui':'electron'})
        health.write_text('{"ready":true,"ui":"electron"}')
        assert_ui_health(health,{'ui':'electron'})

    def test_legacy_smoke_rejects_unexpected_electron(self):
        self.fixture('app/electron/file')
        with self.assertRaises(AssertionError):
            assert_ui_payload(self.root/'app',{'ui':'qt'})


if __name__=='__main__':
    unittest.main()
