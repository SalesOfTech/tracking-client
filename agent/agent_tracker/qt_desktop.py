"""Native Qt Quick window; collection stays in the existing durable worker."""
import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from .core.client import Client, company_code_from_filename, workspace
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .runtime import Worker

os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Fusion')
from .ui.quick.qt import QObject, Property, Signal, Slot, Qt, QTimer, QUrl, QIcon, QFont, QFontDatabase, QApplication, QSystemTrayIcon, QMenu, QQmlApplicationEngine, QCoreApplication, QEvent
from .ui.quick.bridge import Bridge


def system_dark(app):
    hints = app.styleHints()
    if hasattr(hints, 'colorScheme') and hasattr(Qt, 'ColorScheme'):
        scheme = hints.colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    # Qt 5 on Windows does not expose the live application color scheme.
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return not bool(winreg.QueryValueEx(key, 'AppsUseLightTheme')[0])
        except OSError:
            pass
    return app.palette().window().color().lightness() < 128


def appearance_colors(dark):
    return dict(
        dark=dark,
        background='#1c1c1e' if dark else '#ffffff',
        surface='#252527' if dark else '#f5f7fa',
        hover='#303033' if dark else '#ebeff4',
        border='#454548' if dark else '#dce2e9',
        text='#f3f3f4' if dark else '#182333',
        muted='#b6b6bd' if dark else '#536171',
        accent='#78baff' if dark else '#0878db',
        selected='#25384b' if dark else '#e6f1fe',
        primary='#0878db', primaryHover='#076bc4', primaryPressed='#095da9',
        success='#70d7a7' if dark else '#208b65',
        warning='#ecc780' if dark else '#805b2e',
        icon='white' if dark else 'neutral',
    )


class Desktop(QObject):
    appearanceChanged = Signal()

    def __init__(self, client, company_code='', install=None, preview=False, headless=False):
        super().__init__()
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setApplicationName('SOFT Tracking')
        self.app.setQuitOnLastWindowClosed(preview)
        self._colors = appearance_colors(system_dark(self.app))
        self.app.paletteChanged.connect(self.refreshAppearance)
        if hasattr(self.app.styleHints(), 'colorSchemeChanged'):
            self.app.styleHints().colorSchemeChanged.connect(self.refreshAppearance)
        if self.app.platformName() == 'offscreen' and os.name == 'nt':
            for filename in ('segoeui.ttf', 'seguisb.ttf', 'segoeuib.ttf'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / filename))
            self.app.setFont(QFont('Segoe UI', 10))
        self.worker = None if preview else Worker(client)
        self.bridge = Bridge(client, self.worker, install, company_code, preview)
        self.install, self.preview = install, preview
        self.tray = None
        self.closing = False
        self.engine = QQmlApplicationEngine()
        self.engine.rootContext().setContextProperty('bridge', self.bridge)
        self.engine.rootContext().setContextProperty('desktop', self)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'ui/quick/Main.qml')))
        if not self.engine.rootObjects():
            raise RuntimeError('Qt Quick interface could not be loaded')
        self.window = self.engine.rootObjects()[0]
        self.bridge.guideRequested.connect(self.showGuide)
        self.bridge.stopAuthorized.connect(self.authorizedStop)
        self.app.setWindowIcon(QIcon(str(Path(__file__).parent / 'assets/app.ico')))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        if not headless and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.app.windowIcon(), self)
            menu = QMenu()
            self.open_action = menu.addAction(self.bridge.view['labels']['open'], self.show)
            self.guide_action = menu.addAction(self.bridge.view['labels']['guide'], self.showGuide)
            self.tray.setContextMenu(menu)
            self.tray.setToolTip('SOFT Tracking')
            self.tray.activated.connect(lambda reason: self.show() if reason == QSystemTrayIcon.Trigger else None)
            self.tray.show()
            self.menu = menu
            self.bridge.changed.connect(self.refreshTray)
        if self.worker:
            self.worker.start()

    @Property(bool)
    def hideOnClose(self):
        return not self.preview

    @Property('QVariantMap', notify=appearanceChanged)
    def colors(self):
        return self._colors

    def refreshAppearance(self, *_):
        colors = appearance_colors(system_dark(self.app))
        if colors != self._colors:
            self._colors = colors
            self.appearanceChanged.emit()

    def refreshTray(self):
        self.open_action.setText(self.bridge.view['labels']['open'])
        self.guide_action.setText(self.bridge.view['labels']['guide'])

    @Slot()
    def showGuide(self):
        self.window.setProperty('page', 3)
        self.show()

    @Slot()
    def authorizedStop(self):
        self.closing = True

    def show(self):
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def tick(self):
        self.refreshAppearance()
        if self.install:
            stop = read_json(self.install / 'stop-request.json', {})
            if stop.get('token') and stop['token'] == os.environ.get('SOFT_TRACKING_RUN_TOKEN'):
                self.closing = True
            show = self.install / 'show-window.json'
            if show.exists() and not self.closing:
                show.unlink(missing_ok=True)
                self.show()
        if self.closing:
            self.window.hide()
            if self.worker:
                self.worker.stopping.set()
                if self.worker.is_alive():
                    return
            if self.bridge.job and self.bridge.job.is_alive():
                return
            self.app.quit()

    def stop(self):
        self.timer.stop()
        self.bridge.timer.stop()
        self.app.paletteChanged.disconnect(self.refreshAppearance)
        if hasattr(self.app.styleHints(), 'colorSchemeChanged'):
            self.app.styleHints().colorSchemeChanged.disconnect(self.refreshAppearance)
        if self.tray:
            self.tray.hide()
        if self.worker:
            self.worker.stopping.set()
            self.worker.join(timeout=30)
        if self.bridge.job:
            self.bridge.job.join(timeout=15)
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def run(self, start_hidden=False):
        if not start_hidden:
            self.show()
        self.app.processEvents()
        if os.environ.get('SOFT_TRACKING_HEALTH'):
            atomic_json(Path(os.environ['SOFT_TRACKING_HEALTH']), {'ready': True})
        return self.app.exec() if hasattr(self.app, 'exec') else self.app.exec_()


def preview_state(client, language):
    now = int(time.time())
    client.state.set('identity', dict(company_id=1, user_id=1, company_name='Demo company', user_name='Demo employee', device_id=client.device['device_id']))
    client.state.set('language', language)
    client.state.set('policy', dict(tracking=True, policy_expires_at=now+3600, domains=['demo.kommo.com']))
    client.state.set('last_web_delivery', dict(hostname='demo.kommo.com', timestamp=now-75, end_timestamp=now-33, confirmed_at=now-20))
    client.state.set('browser:'+'a'*32, dict(family='Chrome', version='3.1.0.60000', last_seen=now, error=''))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--company-code', default='')
    parser.add_argument('--autostart', action='store_true')
    parser.add_argument('--health-check', action='store_true')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--language', default='ru', choices=['ru','en','cs','uz'])
    parser.add_argument('--screenshot')
    parser.add_argument('--page', type=int, default=0, choices=[0,1,2,3])
    parser.add_argument('--unregistered', action='store_true')
    parser.add_argument('--preview-width', type=int, default=960)
    parser.add_argument('--preview-height', type=int, default=720)
    args = parser.parse_args(argv)
    if args.screenshot and not args.preview:
        parser.error('Screenshots require isolated preview mode')
    install = Path(os.environ['SOFT_TRACKING_INSTALL']) if os.environ.get('SOFT_TRACKING_INSTALL') else None
    profile = read_json(install / 'enrollment.json', {}) if install else {}
    if args.company_code and profile.get('company_code') and args.company_code != profile['company_code']:
        raise ValueError('Conflicting company installation codes')
    code = profile.get('company_code') or args.company_code or company_code_from_filename(sys.executable)
    temporary = tempfile.TemporaryDirectory(prefix='soft-tracking-ui-') if args.preview else None
    root = Path(temporary.name) if temporary else workspace()
    with ExitStack() as stack:
        if temporary:
            stack.callback(temporary.cleanup)
        stack.enter_context(SingleInstance(root / 'agent.lock'))
        client = Client(root)
        if args.preview:
            preview_state(client, args.language)
            if args.unregistered:
                client.state.set('identity', None)
                client.state.set('last_web_delivery', {})
        desktop = Desktop(client, code, None if args.preview else install, preview=args.preview or args.health_check, headless=args.health_check or bool(args.screenshot))
        try:
            if args.preview:
                desktop.window.resize(max(740, args.preview_width), max(580, args.preview_height))
            desktop.window.setProperty('page', args.page)
            if args.health_check:
                desktop.app.processEvents()
                if install:
                    from .core.release_manager import executable_name
                    from .native_host import ALLOWED_ORIGIN, read_message
                    import io
                    payload=b'{"action":"status"}'
                    result=subprocess.run([str(Path(sys.executable).parent / executable_name(native=True)), ALLOWED_ORIGIN],input=struct.pack('=I',len(payload))+payload,capture_output=True,timeout=20,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                    if result.returncode or not read_message(io.BytesIO(result.stdout)).get('ok'):
                        return 1
                if os.environ.get('SOFT_TRACKING_HEALTH'):
                    atomic_json(Path(os.environ['SOFT_TRACKING_HEALTH']), {'ready':True})
                return 0
            if args.screenshot:
                def capture():
                    try:
                        image=desktop.window.grabWindow()
                        if image.isNull():
                            # Qt 5 software rendering exposes item grabs but not window grabs.
                            desktop.capture = desktop.window.contentItem().grabToImage()
                            if desktop.capture:
                                desktop.capture.ready.connect(lambda: desktop.app.exit(0 if desktop.capture.image().save(args.screenshot) else 1))
                                QTimer.singleShot(5000, lambda: desktop.app.exit(1))
                                return
                        saved=not image.isNull() and image.save(args.screenshot)
                    except Exception as error:
                        print('Preview capture failed: '+str(error), file=sys.stderr)
                        saved=False
                    if not saved:
                        desktop.app.exit(1)
                    else:
                        desktop.app.quit()
                QTimer.singleShot(700, capture)
            return desktop.run(args.autostart)
        finally:
            desktop.stop()
            if not desktop.worker or not desktop.worker.is_alive():
                client.close()


if __name__ == '__main__':
    raise SystemExit(main())
