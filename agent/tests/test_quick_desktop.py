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
    from agent_tracker.ui.quick.qt import QApplication, QObject, Qt, QTimer
    from agent_tracker.ui.quick.labels import EXTRA
    from agent_tracker.i18n import LANGUAGES
    QUICK_AVAILABLE = True
except ImportError:
    QUICK_AVAILABLE = False
from agent_tracker.core.client import Client
from agent_tracker.core.files import atomic_json, read_json
from agent_tracker.core.update_control import request_check
from agent_tracker.browser_setup import EXTENSION_PAGES


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

    def test_uninstall_is_async_and_cancellation_keeps_worker_running(self):
        self.bridge.install = Path(self.temporary.name) / 'install'
        child = Mock()
        child.poll.return_value = None
        with patch('agent_tracker.ui.quick.bridge.can_uninstall', return_value=True), \
                patch('agent_tracker.ui.quick.bridge.launch_uninstaller', return_value=child) as launch:
            self.bridge.requestUninstall()
            self.finish_job()
            self.assertTrue(self.bridge.view['uninstalling'])
            self.assertTrue(self.bridge.view['busy'])
            self.bridge.requestUninstall()
            self.bridge.requestStop()
            launch.assert_called_once_with(self.bridge.install)
            child.poll.return_value = 2
            self.bridge.refresh()
            self.assertFalse(self.bridge.view['uninstalling'])
            self.assertEqual('uninstall_cancelled', self.bridge.message)
        child.wait.assert_not_called()
        self.worker.request_graceful_stop.assert_not_called()

    def test_uninstall_is_unavailable_in_preview_or_unsupported_installation(self):
        self.bridge.install = Path(self.temporary.name) / 'install'
        with patch('agent_tracker.ui.quick.bridge.launch_uninstaller') as launch:
            self.bridge.preview = True
            with patch('agent_tracker.ui.quick.bridge.can_uninstall', return_value=True):
                self.bridge.requestUninstall()
                self.bridge.refresh()
                self.assertFalse(self.bridge.view['canUninstall'])
            self.bridge.preview = False
            with patch('agent_tracker.ui.quick.bridge.can_uninstall', return_value=False):
                self.bridge.requestUninstall()
                self.bridge.refresh()
                self.assertFalse(self.bridge.view['canUninstall'])
            launch.assert_not_called()

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

    def test_browser_buttons_copy_only_fixed_addresses_without_launching(self):
        self.app.clipboard().setText('unchanged')
        with patch('agent_tracker.browser_setup.open_extensions_page') as opener, \
                patch('agent_tracker.ui.quick.bridge.QDesktopServices.openUrl') as external:
            self.bridge.openBrowser('https://example.invalid')
            self.assertEqual('unchanged', self.app.clipboard().text())
            for index, language in enumerate(LANGUAGES):
                self.bridge.setLanguage(index)
                for family, address in EXTENSION_PAGES.items():
                    with self.subTest(language=language, family=family):
                        self.bridge.openBrowser(family)
                        self.assertEqual(address, self.app.clipboard().text())
                        self.assertEqual('browser_address_copied', self.bridge.message)
                        expected = self.bridge.view['labels']['browser_address_copied'].format(browser=family, address=address)
                        self.assertEqual(expected, self.bridge.view['browserMessage'])
                        self.assertEqual(expected, self.bridge.view['message'])
                        self.assertIn('Enter', expected)
                        self.assertIsNone(self.bridge.job)
            opener.assert_not_called()
            external.assert_not_called()
        self.app.clipboard().setText('unchanged')
        for mode in ('preview', 'busy'):
            setattr(self.bridge, mode, True)
            self.bridge.openBrowser('Chrome')
            self.assertEqual('unchanged', self.app.clipboard().text())
            setattr(self.bridge, mode, False)

    def test_browser_clipboard_errors_are_localized_and_sanitized(self):
        for index, language in enumerate(LANGUAGES):
            self.bridge.setLanguage(index)
            with patch('agent_tracker.ui.quick.bridge.QApplication.clipboard', side_effect=RuntimeError('private clipboard detail')):
                self.bridge.openBrowser('Firefox')
            self.assertEqual('browser_copy_failed', self.bridge.message)
            self.assertEqual(self.bridge.view['labels']['browser_copy_failed'], self.bridge.view['browserMessage'])
            self.assertNotIn('private clipboard detail', self.bridge.view['message'])

    def installed_root(self):
        root = Path(self.temporary.name) / 'install'
        root.mkdir()
        atomic_json(root / 'current.json', {'version': '3.4.0'})
        atomic_json(root / 'update-status.json', {'state': 'active', 'checked_at': int(time.time())})
        self.bridge.install = root
        self.bridge.refresh()
        return root

    def test_update_check_only_queues_one_local_supervisor_request(self):
        root = self.installed_root()
        self.assertTrue(self.bridge.view['canCheckUpdate'])
        with patch('agent_tracker.ui.quick.bridge.request_check', wraps=request_check) as request, \
                patch('requests.Session.request', side_effect=AssertionError('Qt must not check updates over the network')):
            self.bridge.checkUpdates()
            pending = read_json(root / 'update-request.json')
            self.assertEqual({'id', 'requested_at'}, set(pending))
            self.assertTrue(self.bridge.view['updateBusy'])
            self.assertFalse(self.bridge.view['canCheckUpdate'])
            self.bridge.checkUpdates()
            request.assert_called_once_with(root)
            self.assertEqual(pending, read_json(root / 'update-request.json'))
        self.assertIsNone(self.bridge.job)
        self.assertFalse(self.worker.sync_requested.is_set())
        atomic_json(root / 'update-status.json', {'state': 'active', 'request_id': pending['id'], 'checked_at': int(time.time())})
        self.bridge.refresh()
        self.assertFalse(self.bridge.view['updateBusy'])
        self.assertTrue(self.bridge.view['canCheckUpdate'])
        self.assertEqual(self.bridge.view['labels']['update_current'], self.bridge.view['updateLabel'])
        self.assertTrue(self.bridge.view['updateCheckedAt'])

    def test_update_check_revalidates_enrollment_install_preview_and_busy(self):
        root = self.installed_root()
        identity = self.client.state.get('identity')
        for guard in ('identity', 'install', 'current', 'preview', 'busy', 'checking', 'downloading', 'uninstall'):
            with self.subTest(guard=guard):
                self.client.state.set('identity', None if guard == 'identity' else identity)
                self.bridge.install = None if guard == 'install' else root
                self.bridge.preview = guard == 'preview'
                self.bridge.busy = guard == 'busy'
                if guard == 'current':
                    (root / 'current.json').unlink()
                else:
                    atomic_json(root / 'current.json', {'version': '3.4.0'})
                if guard == 'uninstall':
                    (root / 'uninstall-requested.json').touch()
                atomic_json(root / 'update-status.json', {'state': guard if guard in ('checking', 'downloading') else 'active', 'started_at': int(time.time())})
                with patch('agent_tracker.ui.quick.bridge.request_check') as request:
                    self.bridge.checkUpdates()
                    self.bridge.refresh()
                request.assert_not_called()
                self.assertFalse(self.bridge.view['canCheckUpdate'])
                self.assertFalse((root / 'update-request.json').exists())

    def test_normal_runtime_readiness_environment_is_not_health_check_mode(self):
        root = self.installed_root()
        with patch.dict(os.environ, {'SOFT_TRACKING_HEALTH': str(root / 'runtime.ready')}):
            self.bridge.checkUpdates()
        self.assertTrue((root / 'update-request.json').is_file())

    def test_update_request_race_and_error_feedback(self):
        self.installed_root()
        with patch('agent_tracker.ui.quick.bridge.request_check', return_value=False) as request, \
                patch('agent_tracker.ui.quick.bridge.update_status', side_effect=[
                    {'state': 'active', 'busy': False, 'checked_at': None},
                    {'state': 'checking', 'busy': True, 'checked_at': None}]):
            self.bridge.checkUpdates()
        request.assert_called_once()
        self.assertTrue(self.bridge.view['updateBusy'])
        self.assertFalse(self.bridge.view['canCheckUpdate'])
        self.assertEqual('', self.bridge.view['message'])
        for index, language in enumerate(LANGUAGES):
            self.bridge.setLanguage(index)
            with patch('agent_tracker.ui.quick.bridge.request_check', side_effect=OSError('private install path')):
                self.bridge.checkUpdates()
            self.assertEqual(self.bridge.view['labels']['update_request_failed'], self.bridge.view['message'])
            self.assertNotIn('private install path', self.bridge.view['message'])
            self.assertFalse(self.bridge.view['updateBusy'])
            self.assertTrue(self.bridge.view['canCheckUpdate'])

    def test_update_states_are_localized_and_dashboard_is_removed(self):
        root = self.installed_root()
        for state, label in (('checking', 'checking'), ('active', 'current'), ('installed', 'active'),
                             ('downloading', 'downloading'), ('rolled_back', 'rollback'), ('error', 'error'), ('registration', 'registration')):
            atomic_json(root / 'update-status.json', {'state': state, 'started_at': int(time.time())})
            for index in range(len(LANGUAGES)):
                self.bridge.setLanguage(index)
                self.assertEqual(self.bridge.view['labels']['update_' + label], self.bridge.view['updateLabel'])
        self.assertNotIn('dashboardAvailable', self.bridge.view)
        self.assertNotIn('open_dashboard', self.bridge.view['labels'])
        with patch('agent_tracker.ui.quick.bridge.QDesktopServices.openUrl') as external:
            self.bridge.open('dashboard')
        external.assert_not_called()


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

    def settle(self):
        for _ in range(4):
            self.app.processEvents()
            time.sleep(0.01)

    def test_update_button_loading_disabled_and_localized_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'install'
            root.mkdir()
            atomic_json(root / 'current.json', {'version': '3.4.0'})
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, install=root, preview=True, headless=True)
            try:
                desktop.show()
                desktop.bridge.preview = False
                button = desktop.window.findChild(QObject, 'checkUpdates')
                progress = desktop.window.findChild(QObject, 'updateProgress')
                status = desktop.window.findChild(QObject, 'updateStatus')
                self.assertIsNotNone(button)
                for width, height in ((740, 580), (960, 720)):
                    desktop.window.resize(width, height)
                    for index, language in enumerate(LANGUAGES):
                        desktop.bridge.setLanguage(index)
                        for busy in (False, True):
                            with self.subTest(width=width, language=language, busy=busy):
                                atomic_json(root / 'update-status.json', {'state': 'downloading' if busy else 'active', 'checked_at': int(time.time())})
                                desktop.bridge.refresh()
                                self.settle()
                                self.assertEqual(not busy, button.property('enabled'))
                                self.assertEqual(busy, progress.property('running'))
                                self.assertEqual(busy, progress.property('visible'))
                                self.assertEqual(desktop.bridge.view['labels']['checking' if busy else 'check_updates'], button.property('text'))
                                self.assertEqual(190, button.property('width'))
                                self.assertLessEqual(status.property('contentWidth'), status.property('width') + 1)
                                self.assertLessEqual(status.property('contentHeight'), status.property('height') + 1)
                                self.assertLessEqual(status.property('x') + status.property('width'), button.property('x'))
                                self.assertLessEqual(button.property('x') + button.property('width'), button.parentItem().property('width'))
                atomic_json(root / 'update-status.json', {'state': 'active'})
                desktop.bridge.refresh()
                self.settle()
                button.clicked.emit()
                self.settle()
                self.assertTrue((root / 'update-request.json').is_file())
                self.assertFalse(button.property('enabled'))
                self.assertTrue(progress.property('running'))
            finally:
                desktop.stop()
                client.close()

    def test_vertical_scrollbar_is_only_visible_for_overflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                desktop.show()
                scrollbar = desktop.window.findChild(QObject, 'pageScrollBar')
                self.assertIsNotNone(scrollbar)
                for index, language in enumerate(LANGUAGES):
                    desktop.bridge.setLanguage(index)
                    desktop.window.resize(960, 1600)
                    desktop.window.setProperty('page', 0)
                    self.settle()
                    self.assertGreaterEqual(scrollbar.property('size'), 1)
                    self.assertFalse(scrollbar.property('visible'), language)
                    desktop.window.resize(740, 580)
                    desktop.window.setProperty('page', 3)
                    self.settle()
                    self.assertLess(scrollbar.property('size'), 1)
                    self.assertTrue(scrollbar.property('visible'), language)
            finally:
                desktop.stop()
                client.close()

    def test_main_runtime_autostart_is_hidden_but_manual_launch_shows_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            worker = Mock()
            worker.is_alive.return_value = False
            with patch('agent_tracker.qt_desktop.Worker', return_value=worker):
                desktop = Desktop(client, preview=False, headless=True)
            try:
                self.assertFalse(desktop.window.isVisible())
                with patch.dict(os.environ, {'SOFT_TRACKING_HEALTH': str(Path(tmp) / 'runtime.ready')}):
                    QTimer.singleShot(30, desktop.app.quit)
                    self.assertEqual(0, desktop.run(start_hidden=True))
                    self.assertFalse(desktop.window.isVisible())
                    self.assertEqual({'ready': True}, read_json(Path(tmp) / 'runtime.ready'))
                    QTimer.singleShot(30, desktop.app.quit)
                    self.assertEqual(0, desktop.run(start_hidden=False))
                    self.assertTrue(desktop.window.isVisible())
            finally:
                desktop.stop()
                client.close()

    def test_browser_copy_buttons_work_in_pages_and_guide_without_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(Path(tmp))
            preview_state(client, 'en')
            desktop = Desktop(client, preview=True, headless=True)
            try:
                browser_rows = [dict(family=family, installed=False, signed_package_required=family == 'Firefox',
                                     support_level='signed_package_required' if family == 'Firefox' else 'manual_setup')
                                for family in ('Chrome', 'Edge', 'Yandex', 'Opera', 'Brave', 'Vivaldi', 'Chromium', 'Firefox')]
                desktop.bridge.preview = False
                desktop.show()
                def browser_links(page):
                    guide = desktop.window.findChild(QObject, 'embeddedGuide')
                    return (self.find_visual_item(guide, 'guideSection_browsers') if page == 3 else
                            self.find_visual_item(desktop.window.contentItem(), 'browserLinks'))
                with patch('agent_tracker.ui.quick.bridge.discover_browsers', return_value=browser_rows):
                    desktop.bridge.refresh()
                    for index, language in enumerate(LANGUAGES):
                        desktop.bridge.setLanguage(index)
                        for page in (1, 3):
                            desktop.window.setProperty('page', page)
                            self.settle()
                            self.assertIsNotNone(browser_links(page))
                            for row in browser_rows:
                                family = row['family']
                                with self.subTest(language=language, page=page, family=family):
                                    section = browser_links(page)
                                    button = self.find_visual_item(section, 'copyBrowser_' + family)
                                    self.assertIsNotNone(button)
                                    self.assertTrue(button.property('enabled'))
                                    self.assertEqual('Copy', button.property('glyph'))
                                    self.assertIn(desktop.bridge.view['labels']['browser_copy_address'], button.property('hint'))
                                    address = self.find_visual_item(section, 'browserAddress_' + family)
                                    instruction = self.find_visual_item(section, 'browserInstruction_' + family)
                                    self.assertEqual(EXTENSION_PAGES[family], address.property('text'))
                                    self.assertEqual(desktop.bridge.view['labels']['browser_paste_address'].format(browser=family), instruction.property('text'))
                                    with patch('agent_tracker.browser_setup.open_extensions_page') as opener:
                                        button.clicked.emit()
                                        self.app.processEvents()
                                    opener.assert_not_called()
                                    self.assertEqual(EXTENSION_PAGES[family], self.app.clipboard().text())
                                    feedback = self.find_visual_item(browser_links(page), 'browserFeedback_' + family)
                                    self.assertTrue(feedback.property('visible'))
                                    self.assertIn(EXTENSION_PAGES[family], feedback.property('text'))
                    for mode in ('preview', 'busy'):
                        setattr(desktop.bridge, mode, True)
                        desktop.bridge.refresh()
                        self.settle()
                        self.assertFalse(self.find_visual_item(browser_links(3), 'copyBrowser_Chrome').property('enabled'))
                        setattr(desktop.bridge, mode, False)
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
