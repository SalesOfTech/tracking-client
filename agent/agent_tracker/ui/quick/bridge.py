import re
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from .qt import QObject, Property, Signal, Slot, QTimer, QUrl, QApplication, QDesktopServices
from .labels import messages
from ...browser_health import connections, registration
from ...browser_setup import register_host
from ...core.files import read_json
from ...i18n import LANGUAGES, client_language


class Bridge(QObject):
    changed = Signal()
    completed = Signal(str)

    def __init__(self, client, worker=None, install=None, company_code='', preview=False):
        super().__init__()
        self.client, self.worker, self.install = client, worker, install
        self.company_code, self.preview = company_code, preview
        self.profile = read_json(install / 'enrollment.json', {}) if install else {}
        self.language = client_language(client.state.get('language', ''), self.profile)
        self.busy = False
        self.message = ''
        self.job = None
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
        status = self.client.status()
        policy = status['policy']
        identity = status['identity'] or {}
        browsers = connections(self.client)
        receipt = self.client.state.get('last_web_delivery', {})
        labels = messages(self.language)
        update = read_json(self.install / 'update-status.json', {}) if self.install else {}
        update_key = 'update_' + {'installed':'active', 'rolled_back':'rollback'}.get(update.get('state'), update.get('state', 'checking'))
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
            languageIndex=list(LANGUAGES).index(self.language), version=status['version'],
            company=identity.get('company_name') or self.profile.get('company_name') or labels['company_installer'],
            employee=identity.get('user_name') or labels['not_connected'], enrolled=bool(identity),
            code=self.company_code, needsCode=not bool(self.company_code), busy=self.busy,
            message=labels.get(self.message, labels['connect_failed']) if self.message else '',
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
            dashboardAvailable=any(re.fullmatch(r'[a-z0-9-]+\.(amocrm\.ru|kommo\.com)', host) for host in policy.get('domains', [])),
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
        if self.busy:
            return
        self.busy, self.message = True, 'checking'
        self.refresh()
        def run():
            try:
                result = function()
            except Exception:
                result = 'connect_failed'
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

    @Slot()
    def retry(self):
        self.client.outbox.retry_rejected()
        self.client.state.set('retry_generation', int(time.time()*1000))
        self.check()

    @Slot(str)
    def open(self, target):
        extension = self.install / 'extension' if self.install else Path(__file__).resolve().parents[4] / 'extension'
        if target == 'dashboard':
            host = next((host for host in self.client.policy().get('domains', []) if re.fullmatch(r'[a-z0-9-]+\.(amocrm\.ru|kommo\.com)', host)), '')
            if host:
                QDesktopServices.openUrl(QUrl('https://' + host + '/dashboard/?' + urlencode({'period':'day'})))
            return
        path = extension if target == 'extension' else extension / 'setup.html'
        if path.exists():
            url = QUrl.fromLocalFile(str(path))
            if target != 'extension':
                url.setFragment('lang=' + self.language)
            QDesktopServices.openUrl(url)
        else:
            self.message = 'package_missing'
            self.refresh()
