import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from agent_tracker.qt_desktop import Desktop, preview_state, appearance_colors
    from agent_tracker.ui.quick.bridge import Bridge
    from agent_tracker.ui.quick.qt import QApplication, QObject, Qt
    from agent_tracker.ui.quick.labels import EXTRA
    from agent_tracker.i18n import LANGUAGES
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
                    for page in (0, 1, 2, 3):
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


@unittest.skipUnless(QUICK_AVAILABLE, 'Qt dependencies are installed in native-build jobs')
class QuickActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.client = Client(Path(self.temporary.name))
        self.addCleanup(self.client.close)
        preview_state(self.client, 'en')
        self.worker = Mock(stopping=threading.Event(), sync_requested=threading.Event())
        self.worker.request_graceful_stop.side_effect = self.worker.stopping.set
        self.bridge = Bridge(self.client, self.worker, company_code='a' * 32)
        self.addCleanup(self.bridge.timer.stop)

    def finish_job(self):
        self.assertIsNotNone(self.bridge.job)
        self.bridge.job.join(timeout=5)
        self.assertFalse(self.bridge.job.is_alive())
        self.app.processEvents()

    def test_switch_employee_runs_only_through_background_worker_barrier(self):
        calls = []
        self.worker.switch_employee.side_effect = lambda code, key: calls.append(threading.get_ident())
        with patch.object(self.client, 'activate_employee_switch') as activate:
            self.bridge.switchEmployee('b' * 32, ' cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc ')
            self.finish_job()
        self.worker.switch_employee.assert_called_once_with('a' * 32, 'c' * 64)
        activate.assert_not_called()
        self.assertNotEqual(threading.get_ident(), calls[0])
        self.assertEqual('employee_changed', self.bridge.message)

    def test_switch_validation_and_blocked_browser_are_localized(self):
        self.bridge.switchEmployee('', 'invalid')
        self.worker.switch_employee.assert_not_called()
        self.assertEqual('invalid_employee_key', self.bridge.message)
        self.worker.switch_employee.side_effect = ValueError('employee_switch_not_ready')
        self.bridge.switchEmployee('', 'c' * 64)
        self.finish_job()
        self.assertEqual('employee_switch_not_ready', self.bridge.message)
        self.assertEqual(self.bridge.view['labels']['employee_switch_not_ready'], self.bridge.view['message'])

    def test_authorized_stop_checkpoint_failure_does_not_close_the_ui(self):
        self.worker.request_graceful_stop.side_effect = OSError('disk full')
        authorized = []
        self.bridge.stopAuthorized.connect(lambda: authorized.append(True))
        with patch.object(self.bridge.admin, 'request_stop', return_value={'authorized': True, 'state': 'authorized'}):
            self.bridge.requestStop()
            self.finish_job()
        self.worker.request_graceful_stop.assert_called_once_with()
        self.assertFalse(self.worker.stopping.is_set())
        self.assertEqual([], authorized)
        self.assertEqual('stop_pending_activity', self.bridge.message)

    def test_stop_requires_boolean_true_and_background_authorization(self):
        authorized = []
        self.bridge.stopAuthorized.connect(lambda: authorized.append(True))
        for grant in (False, None, 0, 1, 'true', 'True', True):
            with self.subTest(grant=grant):
                self.worker.stopping.clear()
                authorized.clear()
                calls = []
                def request():
                    calls.append(threading.get_ident())
                    return {'authorized': grant, 'state': 'denied'}
                with patch.object(self.bridge.admin, 'request_stop', side_effect=request):
                    self.bridge.requestStop()
                    self.finish_job()
                self.assertNotEqual(threading.get_ident(), calls[0])
                self.assertEqual(grant is True, self.worker.stopping.is_set())
                self.assertEqual([True] if grant is True else [], authorized)

    def test_failed_stop_and_preview_never_stop_the_worker(self):
        with patch.object(self.bridge.admin, 'request_stop', side_effect=RuntimeError('private error detail')):
            self.bridge.requestStop()
            self.finish_job()
        self.assertFalse(self.worker.stopping.is_set())
        self.assertNotIn('private error detail', self.bridge.view['message'])
        self.bridge.preview = True
        with patch.object(self.bridge.admin, 'request_stop') as request:
            self.bridge.requestStop()
            self.bridge.switchEmployee('', 'c' * 64)
        request.assert_not_called()
        self.worker.switch_employee.assert_not_called()

    def test_browser_setup_opens_only_known_families_in_background(self):
        calls = []
        def opened(family):
            calls.append((family, threading.get_ident()))
            return {'opened': True}
        with patch('agent_tracker.ui.quick.bridge.open_extensions_page', side_effect=opened):
            self.bridge.openBrowser('https://example.invalid')
            self.assertEqual([], calls)
            self.bridge.openBrowser('Yandex')
            self.finish_job()
        self.assertEqual('Yandex', calls[0][0])
        self.assertNotEqual(threading.get_ident(), calls[0][1])
        self.assertEqual('browser_page_opened', self.bridge.message)
        self.bridge.preview = True
        with patch('agent_tracker.ui.quick.bridge.open_extensions_page') as opener:
            self.bridge.openBrowser('Chrome')
        opener.assert_not_called()

    def test_browser_launch_errors_are_localized_and_sanitized(self):
        for error, expected in ((ValueError('browser_not_found'), 'browser_not_found'),
                                (OSError('private executable path'), 'browser_open_failed')):
            with patch('agent_tracker.ui.quick.bridge.open_extensions_page', side_effect=error):
                self.bridge.openBrowser('Firefox')
                self.finish_job()
            self.assertEqual(expected, self.bridge.message)
            self.assertEqual(self.bridge.view['labels'][expected], self.bridge.view['message'])


@unittest.skipUnless(QUICK_AVAILABLE, 'Qt dependencies are installed in native-build jobs')
class QuickLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def find_visual_item(self, parent, name):
        if parent.objectName() == name:
            return parent
        for child in parent.childItems():
            found = self.find_visual_item(child, name)
            if found is not None:
                return found
        return None

    def test_embedded_guide_browser_buttons_use_the_fixed_browser_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                browser_rows = [dict(family=family, installed=True, signed_package_required=family == 'Firefox',
                                     support_level='signed_package_required' if family == 'Firefox' else 'manual_setup')
                                for family in ('Chrome', 'Edge', 'Yandex', 'Opera', 'Brave', 'Vivaldi', 'Chromium', 'Firefox')]
                desktop.bridge.preview = False
                with patch('agent_tracker.ui.quick.bridge.discover_browsers', return_value=browser_rows):
                    desktop.bridge.refresh()
                    desktop.window.setProperty('page', 3)
                    self.app.processEvents()
                    guide = desktop.window.findChild(QObject, 'embeddedGuide')
                    section = self.find_visual_item(guide, 'guideSection_browsers')
                    self.assertIsNotNone(section)
                    for row in browser_rows:
                        button = self.find_visual_item(section, 'openBrowser_' + row['family'])
                        self.assertIsNotNone(button)
                        self.assertTrue(button.property('enabled'))
                        with patch('agent_tracker.ui.quick.bridge.open_extensions_page', return_value={'opened': True}) as opener:
                            button.clicked.emit()
                            desktop.bridge.job.join(timeout=5)
                            self.app.processEvents()
                        opener.assert_called_once_with(row['family'])
            finally:
                desktop.stop()
                client.close()

    def test_pages_render_nonblank_in_all_languages_sizes_and_system_schemes(self):
        output = os.environ.get('SOFT_TRACKING_TEST_SCREENSHOTS')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            warnings = []
            desktop.engine.warnings.connect(lambda messages: warnings.extend(str(message) for message in messages))
            try:
                desktop.show()
                for dark in (False, True):
                    with patch('agent_tracker.qt_desktop.system_dark', return_value=dark):
                        desktop.refreshAppearance()
                        for width, height in ((740, 580), (960, 720)):
                            desktop.window.resize(width, height)
                            for index, code in enumerate(LANGUAGES):
                                desktop.bridge.setLanguage(index)
                                for page in (0, 1, 2, 3):
                                    with self.subTest(dark=dark, width=width, language=code, page=page):
                                        desktop.window.setProperty('page', page)
                                        for _ in range(3):
                                            self.app.processEvents()
                                            time.sleep(0.01)
                                        image = desktop.window.grabWindow()
                                        if image.isNull():
                                            capture = desktop.window.contentItem().grabToImage()
                                            deadline = time.monotonic() + 2
                                            while capture and capture.image().isNull() and time.monotonic() < deadline:
                                                self.app.processEvents()
                                                time.sleep(0.01)
                                            image = capture.image() if capture else image
                                        self.assertFalse(image.isNull())
                                        self.assertEqual((width, height), (image.width(), image.height()))
                                        pixels = {image.pixelColor(x, y).name() for x in range(0, width, 10)
                                                  for y in range(0, height, 10)}
                                        self.assertGreater(len(pixels), 10)
                                        if output:
                                            self.assertTrue(image.save(str(Path(output) / f'{code}-{width}-{page}-{"dark" if dark else "light"}.png')))
                self.assertEqual([], warnings)
            finally:
                desktop.stop()
                client.close()

    def test_embedded_guide_and_language_alignment_at_supported_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                with patch('agent_tracker.ui.quick.bridge.QDesktopServices.openUrl') as external:
                    desktop.window.hide()
                    desktop.bridge.open('guide')
                    self.app.processEvents()
                    self.assertEqual(3, desktop.window.property('page'))
                    self.assertTrue(desktop.window.isVisible())
                    desktop.bridge.open('unknown-target')
                    external.assert_not_called()
                language = desktop.window.findChild(QObject, 'language')
                language_text = desktop.window.findChild(QObject, 'languageText')
                guide_intro = desktop.window.findChild(QObject, 'guideIntro')
                for width, height in ((740, 580), (960, 720)):
                    desktop.window.resize(width, height)
                    for index, (code, name) in enumerate(LANGUAGES.items()):
                        desktop.bridge.setLanguage(index)
                        self.app.processEvents()
                        self.assertEqual(code, client.state.get('language'))
                        self.assertEqual(index, language.property('currentIndex'))
                        self.assertEqual(name, language_text.property('text'))
                        self.assertLessEqual(language_text.property('contentWidth'), language_text.property('width'))
                        self.assertLessEqual(language_text.property('contentHeight'), language_text.property('height'))
                        self.assertEqual(desktop.bridge.view['guide']['intro'], guide_intro.property('text'))
                        self.assertEqual(['install', 'browsers', 'permissions', 'updates', 'troubleshooting'],
                                         [section['id'] for section in desktop.bridge.view['guide']['sections']])
                        self.assertLessEqual(guide_intro.property('contentWidth'), guide_intro.property('width') + 1)
                self.assertFalse(desktop.window.flags() & Qt.FramelessWindowHint)
                for flag in (Qt.WindowTitleHint, Qt.WindowMinimizeButtonHint,
                             Qt.WindowMaximizeButtonHint, Qt.WindowCloseButtonHint):
                    self.assertTrue(desktop.window.flags() & flag)
            finally:
                desktop.stop()
                client.close()

    def test_system_appearance_changes_live_without_saving_a_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                original_theme = client.state.get('theme')
                for dark in (False, True, False):
                    with patch('agent_tracker.qt_desktop.system_dark', return_value=dark):
                        desktop.tick()
                    self.app.processEvents()
                    self.assertEqual(appearance_colors(dark), desktop.colors)
                    self.assertEqual(desktop.colors['background'], desktop.window.color().name())
                    self.assertEqual(original_theme, client.state.get('theme'))
            finally:
                desktop.stop()
                client.close()

    def test_missing_localized_guide_falls_back_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'ru')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                english = desktop.bridge._guides['en']
                desktop.bridge._guides = {'en': english}
                desktop.bridge.refresh()
                self.assertEqual(english, desktop.bridge.view['guide'])
                desktop.bridge._guides = {}
                desktop.bridge.refresh()
                self.assertEqual([], desktop.bridge.view['guide']['sections'])
                self.assertEqual(desktop.bridge.view['labels']['guide_unavailable'], desktop.bridge.view['guide']['intro'])
            finally:
                desktop.stop()
                client.close()

    def test_retry_uses_profile_aware_client_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                with patch.object(client, 'retry_rejected') as retry, patch.object(desktop.bridge, 'check') as check:
                    desktop.bridge.retry()
                retry.assert_called_once_with()
                check.assert_called_once_with()
                self.assertGreater(client.state.get('retry_generation'), 0)
            finally:
                desktop.stop()
                client.close()
