import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_tracker.core.client import Client
from agent_tracker.desktop import Desktop,SingleInstance


class DesktopTests(unittest.TestCase):
    def test_single_instance_does_not_kill_other_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'agent.lock'
            with SingleInstance(path):
                with self.assertRaises(RuntimeError):
                    with SingleInstance(path): pass
            with SingleInstance(path): pass

    def test_registration_window_renders_without_a_server_or_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=Client(Path(tmp))
            client.state.set('language', 'en')
            with patch('agent_tracker.desktop.Worker.start'),patch('agent_tracker.desktop.Worker.join'):
                app=Desktop(client,'a'*32)
                try:
                    app.root.withdraw()
                    app.root.update_idletasks()
                    app.refresh()
                    self.assertEqual('a'*32,app.code.get())
                    self.assertIn('not registered',app.identity.get())
                    self.assertIn('Pending database confirmation: 0',app.detail.get())
                    self.assertGreater(app.root.winfo_reqwidth(),300)
                finally:
                    app.root.destroy()
                    client.close()


if __name__=='__main__': unittest.main()
