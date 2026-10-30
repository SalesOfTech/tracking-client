from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import smoke_macos_locale as smoke
from agent_tracker import i18n


def report(executable, engine, case=smoke.CASES[0]):
    tag, _, language, country = case
    choices = {key: key for key in ('ru', 'cs', 'en', 'uz')}
    return {
        'frozen': True, 'uid': 501, 'euid': 501,
        'executable': str(executable), 'process_executable': str(engine),
        'architecture': smoke.platform.machine(),
        'frozen_root': str(engine.parent.parent / 'Frameworks'),
        'i18n_file': str(engine.parent.parent / 'Frameworks/agent_tracker/i18n.py'),
        'native_tags': [tag], 'native_region': country,
        'system_tags': [tag], 'system_region': country,
        'detected': language, 'automatic_enrollment': language,
        'languages': choices, 'install_labels': choices,
        'overrides': choices, 'saved_overrides': choices, 'manual_enrollment': choices,
    }


class FrozenLocaleEntryTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.root = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        self.marker = self.root / 'locale.json'
        self.entry = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'agent/setup_entry.py'))
        for target, value in [('platform', 'darwin'), ('frozen', True), ('_MEIPASS', str(self.root)),
                              ('argv', ['setup', '--locale-self-test', '--health-check'])]:
            self.context.enter_context(patch.object(sys, target, value, create=True))
        self.context.enter_context(patch.dict(os.environ, SOFT_TRACKING_LOCALE_SMOKE=str(self.marker)))
        self.context.enter_context(patch.object(os, 'getuid', return_value=501, create=True))
        self.context.enter_context(patch.object(os, 'geteuid', return_value=501, create=True))
        self.context.enter_context(patch('psutil.Process', return_value=Mock(exe=lambda: '/frozen/engine')))
        self.native = self.context.enter_context(patch.object(i18n, '_macos_locale', return_value=(['ru-RU'], 'NL')))
        self.context.enter_context(patch.object(i18n, 'system_locale', return_value=(['ru-RU'], 'NL')))

    def test_early_diagnostic_does_not_import_ui_or_extract_payload(self):
        with patch.dict(sys.modules, {'agent_tracker.electron_desktop': None,
                                      'agent_tracker.installer': None,
                                      'agent_tracker.setup_payload': None}):
            self.assertEqual(0, self.entry['entrypoint']())
        result = json.loads(self.marker.read_text())
        self.assertEqual(['ru-RU'], result['native_tags'])
        self.assertEqual('ru', result['detected'])
        self.assertEqual('ru', result['automatic_enrollment'])
        self.assertEqual({key: key for key in i18n.LANGUAGES}, result['saved_overrides'])
        self.assertEqual(result['saved_overrides'], result['overrides'])
        self.assertEqual(result['saved_overrides'], result['manual_enrollment'])
        self.assertEqual(set(i18n.LANGUAGES), set(result['install_labels']))
        self.assertEqual(501, result['euid'])
        self.assertFalse(Path(str(self.marker) + '.phase').exists())

    def test_native_failure_is_not_masked_by_system_fallback_or_dialog(self):
        self.native.side_effect = OSError('private-diagnostic-detail')
        self.assertEqual(1, self.entry['entrypoint']())
        self.assertFalse(self.marker.exists())
        diagnostic = Path(str(self.marker) + '.error').read_text()
        self.assertIn('OSError', diagnostic)
        self.assertNotIn('private-diagnostic-detail', diagnostic)

    def test_source_execution_is_rejected(self):
        with patch.object(sys, 'frozen', False):
            self.assertEqual(1, self.entry['entrypoint']())
        self.native.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_stale_success_marker_cannot_be_reused(self):
        self.marker.write_text('stale')
        self.assertEqual(1, self.entry['entrypoint']())
        self.assertEqual('stale', self.marker.read_text())


class LocaleRunnerTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.root = Path(self.context.enter_context(tempfile.TemporaryDirectory()))
        self.wrapper = self.root / 'SOFT-Tracking-Setup'
        self.engine = self.root / 'Engine.app/Contents/MacOS/engine'

    def test_requires_hosted_nonroot_console_user(self):
        with patch.object(smoke.platform, 'system', return_value='Darwin'), \
                patch.object(os, 'getuid', return_value=501, create=True), \
                patch.object(os, 'geteuid', return_value=501, create=True), \
                patch.dict(os.environ, GITHUB_ACTIONS='true', RUNNER_ENVIRONMENT='github-hosted'), \
                patch.object(smoke, 'run', return_value=Mock(stdout=b'501\n')) as run:
            self.assertEqual(501, smoke.require_runner())
            with patch.dict(os.environ, RUNNER_ENVIRONMENT='self-hosted'):
                with self.assertRaises(RuntimeError):
                    smoke.require_runner()
            with patch.object(os, 'geteuid', return_value=0):
                with self.assertRaises(RuntimeError):
                    smoke.require_runner()
            run.return_value.stdout = b'502\n'
            with self.assertRaises(RuntimeError):
                smoke.require_runner()

    def test_all_languages_validate_actual_cf_and_identity(self):
        for case in smoke.CASES:
            smoke.validate(report(self.wrapper, self.engine, case), self.wrapper, self.engine, 501, case)
        relocated = dict(report(self.wrapper, self.engine),
                         i18n_file=str(self.engine.parent.parent / 'Resources/agent_tracker/i18n.py'))
        smoke.validate(relocated, self.wrapper, self.engine, 501, smoke.CASES[0])
        original = report(self.wrapper, self.engine)
        for key, value in [('frozen', False), ('uid', 0), ('euid', 0), ('executable', '/other'),
                           ('process_executable', '/python'), ('frozen_root', '/source'),
                           ('architecture', 'wrong-architecture'), ('i18n_file', '/source/i18n.py'),
                           ('system_tags', ['en-US']), ('detected', 'en'),
                           ('automatic_enrollment', 'en'), ('languages', {'en': 'English'}),
                           ('overrides', {}), ('saved_overrides', {}), ('manual_enrollment', {})]:
            with self.subTest(key=key):
                invalid = dict(original, **{key: value})
                with self.assertRaises(AssertionError) as error:
                    smoke.validate(invalid, self.wrapper, self.engine, 501, smoke.CASES[0])
                self.assertNotIsInstance(error.exception, smoke.ArgumentPreferencesUnavailable)
        with self.assertRaises(smoke.ArgumentPreferencesUnavailable):
            smoke.validate(dict(original, native_tags=['en-US']), self.wrapper, self.engine, 501, smoke.CASES[0])

    def test_native_arm_and_intel_are_both_accepted(self):
        for architecture in ('arm64', 'x86_64'):
            with self.subTest(architecture=architecture), \
                    patch.object(smoke.platform, 'machine', return_value=architecture):
                smoke.validate(report(self.wrapper, self.engine), self.wrapper, self.engine, 501, smoke.CASES[0])

    def test_process_arguments_need_no_persistent_changes(self):
        results = [report(self.wrapper, self.engine, case) for case in smoke.CASES]
        with patch.object(smoke, 'invoke', side_effect=results) as invoke, \
                patch.object(smoke, 'temporary_preferences') as prefs:
            reports = smoke.probe(self.wrapper, self.engine, 501, self.root, True)
        prefs.assert_not_called()
        self.assertEqual(['arguments'] * 3, [row['preference_mode'] for row in reports])
        self.assertEqual(['-AppleLanguages', '("ru-RU", "en-US")', '-AppleLocale', 'en_NL'],
                         invoke.call_args_list[0].args[2])
        self.assertEqual(3, len({call.args[1] for call in invoke.call_args_list}))

    def test_fallback_only_when_cf_ignores_arguments_and_explicitly_allowed(self):
        expected = report(self.wrapper, self.engine)
        ignored = dict(expected, native_tags=['en-US'])
        with patch.object(smoke, 'CASES', smoke.CASES[:1]), \
                patch.object(smoke, 'invoke', side_effect=[ignored, expected]) as invoke, \
                patch.object(smoke, 'temporary_preferences') as prefs:
            result = smoke.probe(self.wrapper, self.engine, 501, self.root, True)
            prefs.assert_called_once_with(['ru-RU', 'en-US'], 'en_NL')
            self.assertEqual([], invoke.call_args.args[2])
            self.assertEqual('restored-runner-preferences', result[0]['preference_mode'])
        with patch.object(smoke, 'invoke', return_value=ignored), patch.object(smoke, 'temporary_preferences') as prefs:
            with self.assertRaises(smoke.ArgumentPreferencesUnavailable):
                smoke.probe(self.wrapper, self.engine, 501, self.root, False)
            prefs.assert_not_called()

    def test_detection_bug_never_gets_a_preference_fallback(self):
        with patch.object(smoke, 'invoke', return_value=dict(report(self.wrapper, self.engine), detected='en')), \
                patch.object(smoke, 'temporary_preferences') as prefs:
            with self.assertRaises(AssertionError):
                smoke.probe(self.wrapper, self.engine, 501, self.root, True)
            prefs.assert_not_called()

    def test_preferences_restored_on_success_and_failure_including_absence(self):
        for initial in ({}, {'AppleLanguages': ['de-DE'], 'AppleLocale': 'de_DE'}):
            for fail in (False, True):
                with self.subTest(initial=initial, fail=fail):
                    state = copy.deepcopy(initial)
                    def write(key, value):
                        state[key] = value
                    def delete(*args):
                        self.assertEqual(('/usr/bin/defaults', 'delete', '-g'), args[:3])
                        state.pop(args[3])
                    with patch.object(smoke, 'require_runner'), \
                            patch.object(smoke, 'preferences', side_effect=lambda: copy.deepcopy(state)), \
                            patch.object(smoke, 'write_preference', side_effect=write), \
                            patch.object(smoke, 'run', side_effect=delete):
                        try:
                            with smoke.temporary_preferences(['ru-RU'], 'en_NL'):
                                self.assertEqual(['ru-RU'], state['AppleLanguages'])
                                if fail:
                                    raise ValueError('probe failure')
                        except ValueError:
                            self.assertTrue(fail)
                    self.assertEqual(initial, state)

    def test_restore_attempts_both_keys_even_if_one_restore_fails(self):
        saved = {'AppleLanguages': ['de-DE'], 'AppleLocale': 'de_DE'}
        with patch.object(smoke, 'require_runner'), patch.object(smoke, 'preferences', return_value=saved), \
                patch.object(smoke, 'write_preference', side_effect=[None, None, OSError(), None]) as write:
            with self.assertRaisesRegex(RuntimeError, 'restoration failed'):
                with smoke.temporary_preferences(['ru-RU'], 'en_NL'):
                    pass
        self.assertEqual(('AppleLocale', 'de_DE'), write.call_args.args)

    def test_invoke_timeout_stops_owned_process(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired('setup', 180)
        process.poll.return_value = None
        with patch.object(smoke.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(smoke, 'stop_tree') as stop, \
                patch.dict(os.environ, PYTHONPATH='/source', SOFT_TRACKING_SETUP_PAYLOAD='/wrong'):
            with self.assertRaises(subprocess.TimeoutExpired):
                smoke.invoke(self.wrapper, self.root / 'marker', [])
            stop.assert_called_once_with(process)
            process.wait.assert_called_once()
            env = launch.call_args.kwargs['env']
            self.assertNotIn('PYTHONPATH', env)
            self.assertNotIn('SOFT_TRACKING_SETUP_PAYLOAD', env)
            self.assertEqual('en_US.UTF-8', env['LC_ALL'])

    def test_cleanup_removes_only_created_cache(self):
        cache = self.root / 'cache'
        cache.mkdir()
        previous = cache / 'previous'
        previous.mkdir()
        created = cache / 'created'
        created.mkdir()
        (created / 'engine').write_text('owned')
        lock = cache / '.lock'
        lock.touch()
        with patch.object(os, 'getuid', return_value=cache.stat().st_uid, create=True):
            smoke.remove_created_cache(cache, [previous, created, lock], {previous}, True)
        self.assertEqual([previous], list(cache.iterdir()))

    def test_full_probe_unmounts_and_cleans_created_cache_even_on_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                home = self.root / str(fail)
                cache = home / 'Library/Caches/SOFTTrackingSetup-engine'
                evidence = home / 'evidence.json'
                mounts = []
                def run(*args):
                    if args[:2] == ('/usr/bin/hdiutil', 'attach'):
                        mount = args[-2]
                        mounts.append(mount)
                        app = mount / 'SOFT-Tracking-Setup.app/Contents'
                        (app / 'MacOS').mkdir(parents=True)
                        (app / 'MacOS/SOFT-Tracking-Setup').write_bytes(b'compiled-wrapper')
                        (app / 'Resources').mkdir()
                        archive = app / 'Resources/engine.zip'
                        archive.write_bytes(b'sealed-engine-archive')
                        archive.rename(archive.with_name('engine-' + smoke.digest(archive) + '.zip'))
                    return Mock(stdout=b'')
                def probe(executable, engine, uid, root, allow):
                    engine.parent.mkdir(parents=True)
                    engine.write_bytes(b'compiled-engine')
                    (cache / ('.engine-' + engine.parents[3].name + '.lock')).touch()
                    if fail:
                        raise ValueError('native test failed')
                    return [report(executable, engine)]
                with patch.object(smoke, 'require_runner', return_value=501), \
                        patch.object(smoke, 'preferences', return_value={}), \
                        patch.object(smoke.Path, 'home', return_value=home), \
                        patch.object(smoke, 'run', side_effect=run) as command, \
                        patch.object(smoke, 'probe', side_effect=probe), \
                        patch.object(os, 'getuid', return_value=self.root.stat().st_uid, create=True):
                    if fail:
                        with self.assertRaises(ValueError):
                            smoke.check(self.root / 'exact.dmg', evidence, True)
                    else:
                        with patch('builtins.print'):
                            smoke.check(self.root / 'exact.dmg', evidence, True)
                    self.assertEqual(('/usr/bin/hdiutil', 'detach', mounts[0]), command.call_args.args)
                self.assertFalse(cache.exists())
                self.assertFalse(mounts[0].parent.exists())
                self.assertEqual(not fail, evidence.exists())


if __name__ == '__main__':
    unittest.main()
