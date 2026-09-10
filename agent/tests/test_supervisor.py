import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_tracker import supervisor
from agent_tracker.core.files import read_json


class SupervisorTests(unittest.TestCase):
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
