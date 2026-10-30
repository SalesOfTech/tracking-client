import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import supervisor
from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.core.update_control import request_check
from agent_tracker.core.instance import SingleInstance
from agent_tracker.bootstrap import app_path


def retained_320_launcher(root, call):
    """3.2.0 normal-launch lock/retry path from commit 502641a (no marker guard)."""
    with SingleInstance(root / 'supervisor.lock'):
        for _ in range(5):
            result = call([str(app_path(root)), '--supervisor', '--installed-root', str(root)])
            if result != 75:
                return result
        return 1


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        for name in ('report_failure', 'flush_reports'):
            patched = patch.object(supervisor, name)
            setattr(self, name, patched.start())
            self.addCleanup(patched.stop)

    def test_update_error_is_reported_without_leaking_message_into_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            manager = Mock()
            manager.active.return_value = {'version': '3.5.0'}
            manager.session.headers = {}
            error = ValueError('https://private/path?secret=credential')
            manager.check.side_effect = error
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, None, 0]
            self.report_failure.return_value = 'ST-ABCDEF123456'
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child), \
                    patch.object(supervisor.time, 'sleep'):
                self.assertEqual(0, supervisor.main(root))
            self.report_failure.assert_called_once_with(root, 'check', 'unknown', error)
            status = read_json(root / 'update-status.json')
            self.assertEqual('error', status['state'])
            self.assertEqual('ST-ABCDEF123456', status['code'])
            self.assertNotIn('secret', str(status))
            self.assertGreaterEqual(self.flush_reports.call_count, 2)

    def test_health_failure_rolls_back_reports_and_schedules_retry(self):
        for exit_code, expected_stage in ((1, 'health_exit'), (0, 'health_receipt')):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve() / 'install'
                root.mkdir()
                manager = Mock()
                manager.active.return_value = {'version': '3.5.0'}
                manager.session.headers = {}
                manager.check.return_value = {'version': '3.5.1'}
                manager.rollback.side_effect = lambda: atomic_json(root / 'failed.json',
                    dict(version='3.5.1', attempts=1, retry_at=1900))
                state = Mock()
                state.get.return_value = {'device_secret': 'fixture'}
                child = Mock(returncode=0)
                child.poll.return_value = None
                candidate = Mock(returncode=exit_code)
                candidate.poll.return_value = exit_code
                self.report_failure.reset_mock()
                self.report_failure.return_value = 'ST-ABCDEF123456'
                with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                        patch.object(supervisor, 'ClientState', return_value=state), \
                        patch.object(supervisor.subprocess, 'Popen', side_effect=[child, candidate]), \
                        patch.object(supervisor.time, 'time', return_value=1000), \
                        patch.object(supervisor.time, 'sleep'):
                    self.assertEqual(75, supervisor.main(root))
                manager.rollback.assert_called_once()
                manager.confirm.assert_not_called()
                self.assertEqual((root, expected_stage, '3.5.1'), self.report_failure.call_args.args[:3])
                self.assertEqual(1900, read_json(root / 'update-status.json')['retry_at'])
                manager.discard_stage.assert_called_once()

    def test_interrupted_update_reports_recovery_before_starting_old_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            atomic_json(root / 'pending.json', {'version': '3.5.1'})
            manager = Mock()
            manager.active.return_value = {'version': '3.5.0'}
            manager.session.headers = {}
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.return_value = 0
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child):
                self.assertEqual(0, supervisor.main(root))
            manager.rollback.assert_called_once()
            self.report_failure.assert_called_once_with(root, 'recovery', '3.5.1')
            self.flush_reports.assert_called_once_with(root)

    def test_retry_deadline_is_checked_before_the_normal_hourly_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            atomic_json(root / 'failed.json', dict(version='3.5.1', attempts=1, retry_at=1900))
            manager = Mock()
            manager.active.return_value = {'version': '3.5.0'}
            manager.session.headers = {}
            manager.check.return_value = None
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, None, None, 0]
            ticks = iter([0, 899, 900])
            now = [0]
            def tick(_): now[0] = next(ticks)
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child), \
                    patch.object(supervisor.time, 'time', side_effect=lambda: 1000 + now[0]), \
                    patch.object(supervisor.time, 'monotonic', side_effect=lambda: now[0]), \
                    patch.object(supervisor.time, 'sleep', side_effect=tick):
                self.assertEqual(0, supervisor.main(root))
            self.assertEqual(2, manager.check.call_count)

    def test_manual_request_bypasses_hourly_interval_without_spawning_another_updater(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            atomic_json(root / 'current.json', {'version': '3.4.0'})
            manager = Mock()
            manager.active.return_value = {'version': '3.4.0'}
            manager.check.return_value = None
            manager.session.headers = {}
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, None, 0]
            ticks = []
            def tick(_):
                ticks.append(True)
                if len(ticks) == 2:
                    self.assertTrue(request_check(root))
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child) as spawn, \
                    patch.object(supervisor.time, 'monotonic', return_value=100), \
                    patch.object(supervisor.time, 'sleep', side_effect=tick):
                self.assertEqual(0, supervisor.main(root, autostart=True))
            self.assertEqual(manager.check.call_count, 2)
            spawn.assert_called_once()
            self.assertIn('--autostart', spawn.call_args.args[0])
            result = read_json(root / 'update-status.json')
            self.assertEqual(result['state'], 'active')
            self.assertRegex(result['request_id'], '^[a-f0-9]{32}$')
            self.assertFalse((root / 'update-request.json').exists())

    def test_retained_320_launcher_enters_current_payload_but_cannot_start_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            (root / 'current.json').write_text('{"version":"3.2.1"}')
            # Contents are immaterial: even an incomplete intent marker must block startup.
            (root / 'uninstall-requested.json').touch()
            def current_payload(arguments):
                self.assertIn((root / 'versions' / '3.2.1').resolve(), Path(arguments[0]).resolve().parents)
                self.assertEqual(arguments[1:3], ['--supervisor', '--installed-root'])
                return supervisor.main(arguments[3])
            call = Mock(side_effect=current_payload)
            with patch.object(supervisor, 'ReleaseManager') as manager, \
                    patch.object(supervisor, 'ClientState') as state, \
                    patch.object(supervisor.subprocess, 'Popen') as spawn:
                self.assertEqual(0, retained_320_launcher(root, call))
            call.assert_called_once()
            manager.assert_not_called()
            state.assert_not_called()
            spawn.assert_not_called()
            self.assertFalse((root / 'run').exists())
            self.assertFalse((root.parent / 'state.sqlite3').exists())

    def test_marker_arriving_during_initialization_blocks_first_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            manager = Mock()
            manager.session.headers = {}
            def active():
                (root / 'uninstall-requested.json').touch()
                return {'version': '3.2.1'}
            manager.active.side_effect = active
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen') as spawn:
                self.assertEqual(0, supervisor.main(root, autostart=True))
            spawn.assert_not_called()
            state.close.assert_called_once()

    def test_marker_blocks_update_health_runtime_and_rolls_back_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            manager = Mock()
            manager.session.headers = {}
            manager.active.return_value = {'version': '3.2.1'}
            manager.check.return_value = {'version': '3.2.2'}
            manager.download.return_value = Mock()
            manager.activate.side_effect = lambda *_: (root / 'uninstall-requested.json').touch()
            state = Mock()
            state.get.side_effect = lambda key: {'device_secret': 'fixture'} if key == 'device' else {'company_id': 32}
            child = Mock(returncode=0)
            child.poll.return_value = None
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child) as spawn, \
                    patch.object(supervisor.time, 'monotonic', return_value=100), \
                    patch.object(supervisor.time, 'sleep'):
                self.assertEqual(0, supervisor.main(root))
            spawn.assert_called_once()
            self.assertNotIn('--health-check', spawn.call_args.args[0])
            manager.rollback.assert_called_once()
            manager.confirm.assert_not_called()
            child.terminate.assert_not_called()

    def test_pending_uninstall_skips_updates_while_child_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            manager = Mock()
            manager.session.headers = {}
            manager.active.return_value = {'version': '3.2.1'}
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, 0]
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), \
                    patch.object(supervisor, 'ClientState', return_value=state), \
                    patch.object(supervisor.subprocess, 'Popen', return_value=child) as spawn, \
                    patch.object(supervisor.time, 'sleep', side_effect=lambda _: (root / 'uninstall-requested.json').touch()):
                self.assertEqual(0, supervisor.main(root))
            spawn.assert_called_once()
            manager.check.assert_not_called()
            child.terminate.assert_not_called()

    def test_checks_updates_on_first_tick_without_prompt_and_clears_old_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / 'install'
            root.mkdir()
            manager = Mock()
            manager.active.return_value = {'version':'3.1.0'}
            manager.check.return_value = None
            manager.session.headers = {}
            state = Mock()
            state.get.side_effect = lambda key: {'device_secret':'test-secret'} if key == 'device' else {'company_id':32}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, 0]
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), patch.object(supervisor, 'ClientState', return_value=state), patch.object(supervisor.subprocess, 'Popen', return_value=child), patch.object(supervisor.time, 'monotonic', return_value=100), patch.object(supervisor.time, 'sleep'):
                self.assertEqual(0, supervisor.main(root))
            manager.check.assert_called_once()
            self.assertEqual('active', read_json(root/'update-status.json')['state'])
            self.assertFalse((root/'stop-request.json').exists())

    def test_failed_release_remains_visible_when_check_returns_no_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / 'failed.json').write_text('{"version":"3.3.1"}')
            manager = Mock()
            manager.active.return_value = {'version': '3.3.0'}
            manager.check.return_value = None
            manager.session.headers = {}
            state = Mock()
            state.get.return_value = {'device_secret': 'fixture'}
            child = Mock(returncode=0)
            child.poll.side_effect = [None, 0]
            with patch.object(supervisor, 'ReleaseManager', return_value=manager), patch.object(supervisor, 'ClientState', return_value=state), patch.object(supervisor.subprocess, 'Popen', return_value=child), patch.object(supervisor.time, 'sleep'):
                self.assertEqual(0, supervisor.main(root))
            status = read_json(root / 'update-status.json')
            self.assertEqual('rolled_back', status['state'])
            self.assertEqual('3.3.1', status['failed_version'])
