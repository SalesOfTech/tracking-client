"""Electron is an isolated view. Python keeps identity, collection and durable delivery."""
from __future__ import annotations

import argparse
import io
import json
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

from .browser_health import connections, registration
from .core.client import Client, company_code_from_filename, workspace
from .core.files import atomic_json, read_json
from .core.instance import SingleInstance
from .core.release_manager import executable_name
from .electron_links import restore_links
from .i18n import LANGUAGES, client_language, detect_language, translate
from .runtime import Worker

MAX_MESSAGE = 8192


def electron_path(folder):
    relative = ('SoftTrackingUI.exe' if os.name == 'nt' else
                'SoftTrackingUI.app/Contents/MacOS/SoftTrackingUI' if sys.platform == 'darwin' else 'SoftTrackingUI')
    return Path(folder) / relative


def runtime_bundle():
    return Path(sys.executable).resolve().parent / 'electron'


def available():
    return electron_path(runtime_bundle()).is_file()


def validate(action, data):
    fields = {'status': (), 'enroll': ('code', 'key'), 'check': (), 'repair': (), 'retry': (), 'resume': (),
              'open': ('target',), 'preferences': ('language', 'theme'), 'install': ('code',),
              'launch': (), 'ready': ()}
    if action not in fields or not isinstance(data, dict) or set(data) - set(fields[action]):
        raise ValueError('invalid_request')
    if action in ('enroll', 'install') and not re.fullmatch('[a-f0-9]{32}', str(data.get('code', ''))):
        raise ValueError('setup_code_required')
    if action == 'enroll' and not re.fullmatch('[a-f0-9]{64}', str(data.get('key', ''))):
        raise ValueError('invalid_employee_key')
    if action == 'preferences':
        if 'language' in data and data['language'] not in LANGUAGES:
            raise ValueError('invalid_request')
        if 'theme' in data and data['theme'] not in ('system', 'light', 'dark'):
            raise ValueError('invalid_request')
    if action == 'open' and data.get('target') not in ('guide', 'extension', 'dashboard'):
        raise ValueError('invalid_request')


class Controller:
    def __init__(self, client=None, install=None, bundle=None, code='', worker=None, health=False):
        self.client, self.install, self.bundle, self.worker = client, install, bundle, worker
        self.mode = 'installer' if bundle else 'desktop'
        profile = read_json(install / 'enrollment.json', {}) if install else {}
        self.code = profile.get('company_code') or code
        self.language = client_language(client.state.get('language', ''), profile) if client else detect_language()
        self.theme = client.state.get('theme', 'system') if client else 'system'
        if self.theme not in ('system', 'light', 'dark'):
            self.theme = 'system'
        self.busy, self.error, self.message = False, '', ''
        self.job, self.launcher = None, None
        self.phase = 'waiting'
        self.health, self.ready, self.exit_requested = health, False, False

    def snapshot(self):
        error_text = translate(self.language, self.error) if self.error else ''
        view = dict(mode=self.mode, language=self.language, theme=self.theme, code=self.code,
                    busy=self.busy, error=self.error, errorText=error_text, message=self.message, phase=self.phase)
        if not self.client:
            view.update(version=read_json(self.bundle / 'setup-build.json', {}).get('version', ''), enrolled=False)
            return view
        state = self.client.status()
        identity, policy = state['identity'] or {}, state['policy']
        receipt = self.client.state.get('last_web_delivery', {})
        reason = state.get('collection_reason', 'unregistered')
        if reason == 'disabled_policy':
            reason = policy.get('tracking_disabled_reason') or reason
        update = read_json(self.install / 'update-status.json', {}) if self.install else {}
        update_state = update.get('state', 'checking')
        view.update(
            version=state['version'], enrolled=bool(identity), company=identity.get('company_name', ''),
            employee=identity.get('user_name', ''), collection=reason,
            pending=state['queue']['pending'], rejected=state['queue']['rejected'],
            deliveryError=bool(state.get('error')), receipt=receipt,
            browsers=[{key: row.get(key) for key in ('family', 'version', 'connected', 'error', 'last_seen')}
                      for row in connections(self.client)],
            domains=policy.get('domains', []), programs=policy.get('track_processes', []),
            policy={flag: bool(policy.get(flag)) for flag in ('tracking', 'interactions', 'field_values', 'app_inventory')},
            update=update_state if update_state in ('active', 'installed', 'checking', 'registration', 'downloading', 'rolled_back', 'error') else 'checking',
        )
        return view

    def task(self, operation):
        if self.busy or self.health:
            raise ValueError('busy')
        self.busy, self.error, self.message = True, '', ''
        def run():
            try:
                operation()
            except Exception as error:
                # Never expose request bodies, tokens, filesystem paths or server traces.
                allowed = {'setup_code_required', 'setup_company_conflict', 'setup_company_unavailable',
                           'setup_existing_damaged', 'setup_upgrade_failed', 'setup_wrong_target', 'setup_close_required'}
                self.error = str(error) if str(error) in allowed else 'setup_failed' if self.bundle else 'connect_failed'
                if self.bundle:
                    self.phase = 'failed'
            finally:
                self.busy = False
        self.job = threading.Thread(target=run, name='DesktopCommand', daemon=True)
        self.job.start()

    def command(self, action, data):
        validate(action, data)
        if action == 'status':
            return self.snapshot()
        if action == 'ready':
            self.ready = True
            if os.environ.get('SOFT_TRACKING_HEALTH'):
                atomic_json(Path(os.environ['SOFT_TRACKING_HEALTH']), {'ready': True, 'ui': 'electron'})
            return {}
        if action == 'preferences':
            for key in ('language', 'theme'):
                if key in data:
                    setattr(self, key, data[key])
                    if self.client:
                        self.client.state.set(key, data[key])
            return self.snapshot()
        if self.health:
            raise ValueError('invalid_request')
        if action == 'install' and self.bundle:
            if self.code and self.code != data['code']:
                raise ValueError('setup_company_conflict')
            self.code = data['code']
            def install():
                from .installer import install
                self.phase = 'installing'
                self.launcher = install(self.bundle, workspace() / 'install', self.code, language=self.language)
                self.phase = 'complete'
            self.task(install)
        elif action == 'launch' and self.bundle:
            if self.busy or not self.launcher:
                raise ValueError('invalid_request')
            subprocess.Popen([str(self.launcher)])
            self.exit_requested = True
        elif action == 'enroll' and self.client:
            if self.client.state.get('identity'):
                raise ValueError('already_registered')
            if self.code and self.code != data['code']:
                raise ValueError('setup_company_conflict')
            def enroll():
                self.client.enroll(self.code or data['code'], data['key'])
                self.worker.sync_requested.set()
                self.message = 'connected'
            self.task(enroll)
        elif action == 'resume' and self.client and not self.bundle:
            if self.busy or self.client.status().get('collection_reason') != 'paused_local':
                raise ValueError('invalid_request')
            self.client.state.set('paused', False)
            if self.worker:
                self.worker.sync_requested.set()
        elif action in ('check', 'retry', 'repair') and self.client:
            def check():
                if action == 'repair':
                    if not self.install:
                        raise ValueError('package_missing')
                    from .browser_setup import register_host
                    register_host(self.install / executable_name(True, True), self.client.state.root)
                if action == 'retry':
                    self.client.outbox.retry_rejected()
                    self.client.state.set('retry_generation', int(time.time() * 1000))
                if self.worker:
                    self.worker.sync_requested.set()
                self.message = 'check_complete'
            self.task(check)
        elif action == 'open':
            self.open(data['target'])
        else:
            raise ValueError('invalid_request')
        return self.snapshot()

    def open(self, target):
        import webbrowser
        from urllib.parse import urlencode
        if target == 'dashboard':
            if not self.client:
                raise ValueError('invalid_request')
            domain = next((host for host in self.client.policy().get('domains', [])
                           if re.fullmatch(r'[a-z0-9-]+\.(amocrm\.ru|kommo\.com)', host)), '')
            if domain:
                webbrowser.open('https://' + domain + '/dashboard/?' + urlencode({'period': 'day'}))
            return
        folder = self.bundle / 'guide' if self.bundle else self.install / 'extension' if self.install else None
        if not folder:
            raise ValueError('package_missing')
        path = folder if target == 'extension' else folder / 'setup.html'
        if not path.exists():
            raise ValueError('package_missing')
        if target == 'extension':
            if os.name == 'nt':
                os.startfile(str(path))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        else:
            webbrowser.open(path.as_uri() + '#lang=' + self.language)


def serve(controller, folder, hidden=False):
    restore_links(folder)
    executable = electron_path(folder)
    if not executable.is_file():
        raise RuntimeError('Missing packaged Electron interface')
    env = dict(os.environ, SOFT_TRACKING_UI_MODE=controller.mode,
               SOFT_TRACKING_UI_HEALTH='1' if controller.health else '0',
               SOFT_TRACKING_UI_HIDDEN='1' if hidden else '0')
    env.pop('ELECTRON_RUN_AS_NODE', None)
    child = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=None if controller.health else subprocess.DEVNULL, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    incoming = queue.Queue(maxsize=32)
    def receive():
        try:
            while True:
                line = child.stdout.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE or not line.endswith(b'\n'):
                    incoming.put(None)
                    return
                incoming.put(line)
        finally:
            incoming.put(None)
    reader = threading.Thread(target=receive, name='DesktopIPC', daemon=True)
    reader.start()
    def send(value):
        if child.stdin.closed:
            return
        child.stdin.write(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii') + b'\n')
        child.stdin.flush()
    deadline = time.monotonic() + 30
    closing_deadline = None
    stopping = False
    try:
        while child.poll() is None:
            if closing_deadline is not None and time.monotonic() > closing_deadline:
                raise RuntimeError('Desktop interface shutdown timed out')
            if (not controller.ready or controller.health) and time.monotonic() > deadline:
                raise RuntimeError('Desktop interface startup or health check timed out')
            if controller.install:
                stop = read_json(controller.install / 'stop-request.json', {})
                stopping = stopping or bool(stop.get('token') and stop['token'] == os.environ.get('SOFT_TRACKING_RUN_TOKEN'))
                show = controller.install / 'show-window.json'
                if show.exists():
                    show.unlink(missing_ok=True)
                    send({'event': 'show'})
            if stopping or controller.exit_requested:
                if controller.worker:
                    controller.worker.stopping.set()
                if (not controller.worker or not controller.worker.is_alive()) and not controller.busy:
                    send({'event': 'shutdown'})
            try:
                line = incoming.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            request_id = None
            try:
                request = json.loads(line)
                if request == {'event': 'closing'}:
                    child.stdin.close()
                    if closing_deadline is None:
                        closing_deadline = time.monotonic() + 10
                    continue
                request_id = request.get('id')
                if type(request_id) is not int or request_id < 1 or set(request) != {'id', 'action', 'input'}:
                    raise ValueError('invalid_request')
                result = controller.command(request['action'], request['input'])
                send({'id': request_id, 'ok': True, 'data': result})
            except (ValueError, TypeError, AttributeError):
                send({'id': request_id, 'ok': False, 'error': 'invalid_request'})
            except Exception:
                send({'id': request_id, 'ok': False, 'error': 'connect_failed'})
        child.wait(timeout=10)
        return child.returncode if controller.ready else 1
    finally:
        try:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
        finally:
            child.stdin.close()
            child.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--autostart', action='store_true')
    parser.add_argument('--health-check', action='store_true')
    parser.add_argument('--company-code', default='')
    args = parser.parse_args(argv)
    install = Path(os.environ['SOFT_TRACKING_INSTALL']) if os.environ.get('SOFT_TRACKING_INSTALL') else None
    with ExitStack() as stack:
        stack.enter_context(SingleInstance(workspace() / 'agent.lock'))
        client = Client(workspace())
        worker = None if args.health_check else Worker(client)
        controller = Controller(client, install, code=args.company_code, worker=worker, health=args.health_check)
        try:
            if args.health_check and install:
                from .native_host import ALLOWED_ORIGIN, read_message
                payload = b'{"action":"status"}'
                response = subprocess.run([str(Path(sys.executable).parent / executable_name(True)), ALLOWED_ORIGIN],
                                          input=struct.pack('=I', len(payload)) + payload, capture_output=True, timeout=20,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if response.returncode or not read_message(io.BytesIO(response.stdout)).get('ok'):
                    return 1
            if worker:
                worker.start()
            return serve(controller, runtime_bundle(), args.autostart)
        finally:
            if worker:
                worker.stopping.set()
                worker.join(timeout=30)
            if controller.job:
                controller.job.join(timeout=30)
            if (not worker or not worker.is_alive()) and not controller.busy:
                client.close()


def installer_main():
    from .installer import bootstrap_code
    parser = argparse.ArgumentParser()
    parser.add_argument('--health-check', action='store_true')
    args = parser.parse_args()
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload'
    try:
        code = bootstrap_code(sys.executable)
    except Exception:
        code = ''
    controller = Controller(bundle=bundle, code='' if args.health_check else code, health=args.health_check)
    try:
        return serve(controller, bundle / 'electron')
    finally:
        if controller.job:
            controller.job.join()
