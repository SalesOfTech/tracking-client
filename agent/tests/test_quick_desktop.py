import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from agent_tracker.qt_desktop import Desktop, preview_state
    from agent_tracker.ui.quick.qt import QApplication, QObject
    from agent_tracker.ui.quick.labels import EXTRA
    QUICK_AVAILABLE = True
except ImportError:
    QUICK_AVAILABLE = False
from agent_tracker.core.client import Client


@unittest.skipUnless(QUICK_AVAILABLE, 'Qt dependencies are installed in native-build jobs')
class QuickDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_localized_window_navigation_clipboard_and_truthful_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'ru')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                bridge = desktop.bridge
                for language in ('en', 'ru', 'cs', 'uz'):
                    client.state.set('language', language)
                    bridge.language = language
                    bridge.refresh()
                    self.app.processEvents()
                    view = bridge.view
                    self.assertEqual(language, view['language'])
                    self.assertTrue(view['healthy'])
                    self.assertTrue(view['browserReady'])
                    self.assertEqual(42, view['duration'])
                    for key in EXTRA: self.assertTrue(view['labels'][key])
                    for page in (0, 1, 2):
                        desktop.window.setProperty('page', page)
                        self.app.processEvents()
                        self.assertEqual(page, desktop.window.property('page'))
                self.app.clipboard().setText('  abc123  ')
                self.assertEqual('abc123', bridge.paste())
                client.state.set('policy', {'tracking':False,'policy_expires_at':time.time()+60})
                bridge.refresh()
                self.assertFalse(bridge.view['healthy'])
                self.assertNotEqual(bridge.view['labels']['received'], bridge.view['deliveryLabel'])
                client.state.set('last_web_delivery', {})
                bridge.refresh()
                self.assertFalse(bridge.view['received'])
                client.state.set('identity', None)
                bridge.refresh()
                self.assertFalse(bridge.view['enrolled'])
                self.assertIsNotNone(desktop.window.findChild(QObject, 'employeeKey'))
            finally:
                desktop.stop()
                client.close()
