import re
import threading
import time
from pathlib import Path

from .qt import QObject, Property, Signal, Slot, QTimer, QUrl, QApplication, QDesktopServices
from .labels import messages
from ...admin_control import AdminControl
from ...browser_health import connections, registration
from ...browser_setup import BROWSER_FAMILIES, EXTENSION_PAGES, discover_browsers, register_host
from ...core.files import read_json
from ...core.update_control import update_status, request_check
from ...i18n import LANGUAGES, client_language
from ...uninstaller import can_uninstall, launch_uninstaller


class Bridge(QObject):
    changed = Signal()
    completed = Signal(str)
    guideRequested = Signal()
    stopAuthorized = Signal()

    def __init__(self, client, worker=None, install=None, company_code='', preview=False):
        super().__init__()
        self.client, self.worker, self.install = client, worker, install
        self.admin = AdminControl(install)
        self.company_code, self.preview = company_code, preview
        self.profile = read_json(install / 'enrollment.json', {}) if install else {}
        self._guides = read_json(Path(__file__).resolve().parents[2] / 'assets/guide.json', {})
        self.language = client_language(client.state.get('language', ''), self.profile)
        self.busy = False
        self.message = ''
        self.copied_browser = ''
        self.job = None
        self.uninstaller = None
        self._view = {}
        self.completed.connect(self.finish)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()

    @Property('QVariantMap', notify=changed)
    def view(self):
        return self._view

    @Slot()
    def refresh(self):
        if self.uninstaller is not None:
            result = self.uninstaller.poll()
            if result is not None:
                self.uninstaller = None
                self.message = 'uninstall_cancelled' if result == 2 else 'uninstall_requested' if result == 0 else 'uninstall_failed'
        status = self.client.status()
        policy = status['policy']
        identity = status['identity'] or {}
        browsers = connections(self.client)
        receipt = self.client.state.get('last_web_delivery', {})
        labels = messages(self.language)
        update = update_status(self.install)
        update_key = 'update_' + {'active':'current', 'installed':'active', 'rolled_back':'rollback'}.get(update['state'], update['state'])
        message = labels.get(self.message, labels['connect_failed']) if self.message else ''
        if self.message == 'browser_address_copied':
            message = message.format(browser=self.copied_browser, address=EXTENSION_PAGES[self.copied_browser])
        browser_setup = [dict(family=family, installed=False, signed_package_required=family == 'Firefox',
                              support_level='signed_package_required' if family == 'Firefox' else 'manual_setup')
                         for family in BROWSER_FAMILIES] if self.preview else discover_browsers()
        reason = status.get('collection_reason', 'unregistered')
        if reason == 'disabled_policy':
            reason = policy.get('tracking_disabled_reason') or reason
        received = bool(receipt)
        healthy = bool(policy.get('tracking') and received and time.time() - receipt.get('end_timestamp', 0) < 180 and not status['error'])
        delivery = reason if reason not in ('recording',) else 'received' if received else 'waiting_session'
        domain = receipt.get('hostname', '')
        time_label = lambda value: time.strftime('%d.%m %H:%M:%S', time.localtime(value)) if value else ''
        view = dict(
            labels=labels, language=self.language, languages=list(LANGUAGES.values()),
            guide=self._guides.get(self.language) or self._guides.get('en') or
                  dict(intro=labels['guide_unavailable'], note='', sections=[]),
            admin=self.admin.status(), canManage=bool(self.worker) and not self.preview,
            canUninstall=bool(self.worker and not self.preview and self.install and can_uninstall(self.install)),
            uninstalling=self.uninstaller is not None,
            browserSetup=[dict(row, address=EXTENSION_PAGES[row['family']],
                               instruction=labels['browser_paste_address'].format(browser=row['family']))
                          for row in browser_setup if row.get('family') in EXTENSION_PAGES],
            languageIndex=list(LANGUAGES).index(self.language), version=status['version'],
            company=identity.get('company_name') or self.profile.get('company_name') or labels['company_installer'],
            employee=identity.get('user_name') or labels['not_connected'], enrolled=bool(identity),
            code=self.company_code, needsCode=not bool(self.company_code), busy=self.busy or self.uninstaller is not None,
            message=message,
            browserMessage=message if self.message in ('browser_address_copied', 'browser_copy_failed') else '',
            copiedBrowser=self.copied_browser if self.message in ('browser_address_copied', 'browser_copy_failed') else '',
            canCopyBrowser=not self.preview and not self.busy,
            browserReady=any(row['connected'] and not row.get('error') for row in browsers),
            browserName=next((row['family'] for row in browsers if row['connected']), ''),
            browsers=[dict(family=row['family'], version=row['version'], connected=row['connected'],
                           label=labels['browser_storage_error' if row.get('error') else 'browser_connected' if row['connected'] else 'browser_stale'],
                           lastContact=time_label(row['last_seen'])) for row in browsers],
            received=received, healthy=healthy, deliveryLabel=labels.get(delivery, labels['needs_attention']),
            hostname=domain, confirmedAt=time_label(receipt.get('confirmed_at', 0)),
            sessionStart=time_label(receipt.get('timestamp', 0)), sessionEnd=time_label(receipt.get('end_timestamp', 0)),
            duration=max(0, receipt.get('end_timestamp', 0)-receipt.get('timestamp', 0)),
            pending=status['queue']['pending'], rejected=status['queue']['rejected'],
            error=labels['server_unavailable'] if status['error'] else '',
            policy=[dict(name=labels[key], enabled=bool(policy.get(flag))) for key,flag in
                    [('web_time','tracking'),('clicks','interactions'),('fields','field_values'),('programs','app_inventory')]],
            domains=policy.get('domains', []),
            updateLabel=labels.get(update_key, labels['update_unknown']),
            updateBusy=update['busy'], updateCheckedAt=time_label(update['checked_at']),
            canCheckUpdate=bool(identity and self.install and (self.install / 'current.json').is_file()
                                and not (self.install / 'uninstall-requested.json').exists()
                                and not self.preview and not self.busy and not update['busy']),
        )
        if view != self._view:
            self._view = view
            self.changed.emit()

    @Slot(int)
    def setLanguage(self, index):
        if 0 <= index < len(LANGUAGES):
            self.language = list(LANGUAGES)[index]
            self.client.state.set('language', self.language)
            self.refresh()

    def task(self, function):
        if self.busy or self.uninstaller is not None:
            return
        self.busy, self.message = True, 'checking'
        self.refresh()
        def run():
            try:
                result = function()
            except Exception as error:
                code = str(error)
                result = code if code in ('employee_switch_not_ready', 'employee_switch_pending_activity',
                                         'employee_switch_conflict', 'employee_company_mismatch') else 'connect_failed'
            self.completed.emit(result or 'check_complete')
        self.job = threading.Thread(target=run, name='DesktopAction', daemon=True)
        self.job.start()

    @Slot(str)
    def finish(self, message):
        self.busy, self.message = False, message
        self.refresh()

    @Slot(str, str)
    def enroll(self, code, key):
        if self.preview or self.client.state.get('identity'):
            return
        code = self.company_code or code.strip()
        if not re.fullmatch('[a-f0-9]{64}', key.strip()):
            self.message = 'invalid_employee_key'
            self.refresh()
            return
        if not re.fullmatch('[a-f0-9]{32}', code):
            self.message = 'setup_company_unavailable'
            self.refresh()
            return
        def connect():
            self.client.enroll(code, key.strip())
            self.worker.sync_requested.set()
            return 'connected'
        self.task(connect)

    @Slot(str, str)
    def switchEmployee(self, code, key):
        if self.preview or not self.worker or not self.client.state.get('identity') or self.busy:
            return
        code = self.company_code or code.strip()
        if not re.fullmatch('[a-f0-9]{64}', key.strip()):
            self.message = 'invalid_employee_key'
            self.refresh()
            return
        if not re.fullmatch('[a-f0-9]{32}', code):
            self.message = 'setup_company_unavailable'
            self.refresh()
            return
        employee_key = key.strip()
        def switch():
            self.worker.switch_employee(code, employee_key)
            return 'employee_changed'
        self.task(switch)

    @Slot()
    def requestStop(self):
        if self.preview or not self.worker or self.busy or self.uninstaller is not None:
            return
        def stop():
            try:
                result = self.admin.request_stop()
            except Exception:
                return 'stop_error'
            if not isinstance(result, dict):
                return 'stop_error'
            if result.get('authorized') is True:
                try:
                    self.worker.request_graceful_stop()
                except Exception:
                    return 'stop_pending_activity'
                self.stopAuthorized.emit()
                return 'stop_authorized'
            state = result.get('state')
            return 'stop_' + state if state in ('cancelled', 'denied', 'timed_out', 'unavailable', 'busy') else 'stop_error'
        self.task(stop)

    @Slot()
    def requestUninstall(self):
        if (self.preview or not self.worker or self.busy or self.uninstaller is not None
                or not self.install or not can_uninstall(self.install)):
            return
        def remove():
            try:
                self.uninstaller = launch_uninstaller(self.install)
            except Exception:
                return 'uninstall_failed'
            return 'uninstall_requested'
        self.task(remove)

    @Slot()
    def check(self):
        def check():
            if self.worker:
                self.worker.sync_requested.set()
            if not self.preview and self.install:
                if not all(valid for _, valid in registration(self.install, self.client.state.root)):
                    return 'browser_repair'
            return 'check_complete'
        self.task(check)

    @Slot()
    def checkUpdates(self):
        if (self.preview or self.busy or not self.client.state.get('identity') or not self.install
                or not (self.install / 'current.json').is_file()
                or (self.install / 'uninstall-requested.json').exists()):
            return
        try:
            if not update_status(self.install)['busy']:
                request_check(self.install)
                self.message = ''
        except Exception:
            self.message = 'update_request_failed'
        self.refresh()

    @Slot()
    def repair(self):
        def repair():
            if not self.install or self.preview:
                return 'package_missing'
            import os
            register_host(self.install / ('SoftTrackingHost.exe' if os.name == 'nt' else 'soft-tracking-host'), self.client.state.root)
            return 'browser_host_ready'
        self.task(repair)

    @Slot(result=str)
    def paste(self):
        return QApplication.clipboard().text().strip()

    @Slot(str)
    def openBrowser(self, family):
        if self.preview or self.busy or family not in EXTENSION_PAGES:
            return
        self.copied_browser = family
        try:
            QApplication.clipboard().setText(EXTENSION_PAGES[family])
            self.message = 'browser_address_copied'
        except Exception:
            self.message = 'browser_copy_failed'
        self.refresh()

    @Slot()
    def retry(self):
        self.client.retry_rejected()
        self.client.state.set('retry_generation', int(time.time()*1000))
        self.check()

    @Slot(str)
    def open(self, target):
        if target == 'guide':
            self.guideRequested.emit()
            return
        if target != 'extension':
            return
        extension = self.install / 'extension' if self.install else Path(__file__).resolve().parents[4] / 'extension'
        path = extension
        if path.exists():
            url = QUrl.fromLocalFile(str(path))
            QDesktopServices.openUrl(url)
        else:
            self.message = 'package_missing'
            self.refresh()
