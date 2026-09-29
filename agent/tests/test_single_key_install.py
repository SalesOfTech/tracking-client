import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agent_tracker.electron_desktop import Controller, validate


class SingleKeyTests(unittest.TestCase):
    def test_protocol_accepts_one_key_and_old_code(self):
        validate('install', {'code': 'c' * 64})
        validate('install', {'code': 'a' * 32})
        validate('enroll', {'key': 'c' * 64})
        with self.assertRaises(ValueError):
            validate('install', {'code': 'short'})

    def test_activation_precedes_legacy_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = Controller(bundle=root)
            controller.task = lambda fn: fn()
            calls = []
            client = Mock()
            client.state.get.return_value = None
            def enrolled(*args):
                calls.append('enroll')
                return {'company_id':32,'user_id':100}
            client.enroll.side_effect = enrolled
            with patch('agent_tracker.electron_desktop.workspace', return_value=root), \
                 patch('agent_tracker.core.client.Client', return_value=client), \
                 patch('agent_tracker.installer.resolve_employee_key', return_value={'company_code': 'a'*32, 'company_id':32,'user_id':100}), \
                 patch('agent_tracker.installer.install', return_value=root/'launcher') as install, \
                 patch('agent_tracker.legacy_migration.replace_current_user', side_effect=lambda *args: calls.append('retire')):
                controller.command('install', {'code': 'c'*64})
            self.assertFalse(install.call_args.kwargs['retire_legacy'])
            self.assertEqual(calls, ['enroll','retire'])
            self.assertEqual(client.close.call_count, 2)
            self.assertEqual(controller.phase, 'complete')
