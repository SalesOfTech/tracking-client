import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import supervisor
from agent_tracker.core.files import read_json
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
    def test_retained_320_launcher_enters_current_payload_but_cannot_start_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'install'
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
            root = Path(tmp) / 'install'
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
            root = Path(tmp) / 'install'
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
            root = Path(tmp) / 'install'
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
            root = Path(tmp) / 'install'
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
