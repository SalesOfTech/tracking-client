"""Native automatic-update acceptance: python tools/smoke_auto_update.py OLD NEW.

OLD/NEW have the same layout as smoke_update.py (verified setup-payload directories;
NEW also has manifest.json and release-VERSION.zip). The caller must authenticate
the published baseline/candidate artifacts, as accept_release.download does.
Only baselines 3.4.2 and 3.5.0 are supported, inferred from verified OLD metadata.
Run on the matching native desktop CI runner
(Linux needs a display), with the build requirements and OpenSSL on PATH.

No candidate installer or source ReleaseManager drives the upgrade. The original
frozen launcher runs the original frozen supervisor, including its periodic check,
download, signature/checksum validation, graceful stop, health gate and restart.
A loopback-only CONNECT fixture terminates TLS for the real allowed hostnames;
REQUESTS_CA_BUNDLE trusts its ephemeral certificate in child processes only.
Signed envelopes/URLs/archives are served unchanged. No hosts file, system CA,
shipping binary, production service, or OS integration is modified.

This tests automatic updates, not real enrollment, production connectivity, OS
startup registration, or retrying the SAME failed version. A legacy failed.json
for the baseline must not block the NEW version. Unit fixtures are not native acceptance.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import psutil

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_BASELINES = frozenset({'3.4.2', '3.5.0'})
HOSTS = ('tracking.salesoftech.com', 'tracking.salesof.tech')
STAGE = 'arguments'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def client_modules():
    # These helpers only prepare/inspect fixtures; the update runs in frozen code.
    source = Path(os.environ.get('TRACKING_SMOKE_CLIENT_ROOT', ROOT)).resolve() / 'agent'
    sys.path.insert(0, str(source))
    from agent_tracker.core import client, event_queue, files, signed_updates
    for name, module in tuple(sys.modules.items()):
        if name.startswith('agent_tracker') and getattr(module, '__file__', None):
            require(Path(module.__file__).resolve().is_relative_to(source), 'Mixed client checkout imports')
    return client.ClientState, event_queue.EventQueue, files.atomic_json, signed_updates


def executable(native=False, bootstrap=False):
    if os.name == 'nt':
        return ('SoftTrackingHost' if native else 'SoftTracking') + ('' if bootstrap else 'App') + '.exe'
    return ('soft-tracking-host' if native else 'soft-tracking') + ('' if bootstrap else '-app')


def prepare_install(old, new, root, signed, atomic_json):
    bundle = old / 'setup-payload'
    build = read_json(bundle / 'setup-build.json')
    require(isinstance(build, dict) and isinstance(build.get('version'), str)
            and build['version'] in SUPPORTED_BASELINES, 'Unsupported automatic-update baseline')
    old_version = build['version']
    keys = read_json(bundle / 'trusted-update-keys.json')
    baseline = signed.verify_manifest(read_json(bundle / 'manifest.json'), keys, '0.0.0',
                                      build['os'], build['architecture'])
    require(baseline['version'] == old_version, 'Signed baseline does not match setup metadata')
    candidate_bytes = (new / 'manifest.json').read_bytes()
    candidate = signed.verify_manifest(json.loads(candidate_bytes), keys, old_version,
                                       build['os'], build['architecture'])
    require(candidate['target'] == baseline['target'] == build['target'], 'Release target mismatch')
    require(signed.runtime_compatible(baseline) and signed.runtime_compatible(candidate),
            'Native runner does not meet release OS requirements')
    from agent_tracker.core.target import runtime_target
    require(runtime_target() == (baseline['os'], baseline['architecture']), 'Wrong native runner')
    url = urlsplit(candidate['url'])
    require(url.scheme == 'https' and url.hostname in HOSTS and url.port in (None, 443)
            and not url.username and not url.password and not url.fragment, 'Untrusted archive origin')
    archive = new / ('release-' + candidate['version'] + '.zip')
    require(archive.stat().st_size == candidate['size'] and digest(archive) == candidate['sha256'],
            'Candidate archive does not match signed manifest')
    root.mkdir(mode=0o700)
    payload = root / 'versions' / old_version
    signed.stage_archive(bundle / 'release.zip', baseline, payload)
    installed = read_json(payload / 'build.json')
    require(installed['version'] == old_version and installed['target'] == baseline['target']
            and installed.get('launcher_protocol') == 1, 'Invalid baseline installation layout')
    for native in (False, True):
        name = executable(native, bootstrap=True)
        shutil.copy2(payload / 'launcher' / name, root / name)
    shutil.copytree(payload / 'extension', root / 'extension')
    atomic_json(root / 'current.json', {'version': old_version})
    atomic_json(root / 'trusted-update-keys.json', keys)
    atomic_json(root / 'enrollment.json', {'company_code': 'a' * 32})
    atomic_json(root / 'failed.json', {'version': old_version, 'time': int(time.time())})
    return installed, candidate, candidate_bytes, archive


def make_certificate(directory):
    """A disposable, self-signed TLS trust anchor, never installed system-wide."""
    openssl = shutil.which('openssl')
    require(openssl is not None, 'OpenSSL is required for the local TLS fixture')
    config, cert, key = (directory / name for name in ('tls.cnf', 'tls.pem', 'tls.key'))
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n'
                      '[dn]\nCN=Automatic update acceptance\n[extensions]\n'
                      'basicConstraints=critical,CA:TRUE\n'
                      'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n'
                      'extendedKeyUsage=serverAuth\nsubjectAltName=' +
                      ','.join('DNS:' + host for host in HOSTS) + '\n', encoding='ascii')
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                    '-config', str(config), '-keyout', str(key), '-out', str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    key.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return cert, context


class Fixture:
    def __init__(self, manifest, envelope, archive, device):
        self.manifest = manifest
        self.envelope = envelope
        self.archive = archive
        self.device = device
        self.catalog = '/client/v3/releases/' + manifest['target'] + '/manifest.json'
        parsed = urlsplit(manifest['url'])
        self.download = parsed.path + ('?' + parsed.query if parsed.query else '')
        self.release = threading.Event()
        self.catalog_seen = threading.Event()
        self.records = []
        self.lock = threading.Lock()

    def record(self, kind, version=''):
        with self.lock:
            self.records.append((kind, version))

    def seen(self, kind, version=None):
        with self.lock:
            return any(row[0] == kind and (version is None or row[1] == version) for row in self.records)


class QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never put Authorization, URLs or captured client text in CI output.

    def reply(self, status, payload=b'{}', content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class OriginHandler(QuietHandler):
    def authenticated(self):
        fixture = self.server.fixture
        host = self.headers.get('Host', '')
        if host not in (*HOSTS, *(name + ':443' for name in HOSTS)):
            self.reply(421)
            return False
        if self.headers.get('Authorization') != 'Bearer ' + fixture.device['device_secret']:
            fixture.record('unauthorized')
            self.reply(401)
            return False
        return True

    def do_GET(self):
        if not self.authenticated():
            return
        fixture = self.server.fixture
        if self.path == fixture.catalog:
            version = self.headers.get('X-Tracking-Client-Version', '')
            fixture.record('catalog', version)
            fixture.catalog_seen.set()
            # Let the harness observe the old GUI before releasing its first check.
            if not fixture.release.wait(20):
                self.reply(503)
                return
            self.reply(200, fixture.envelope)
        elif self.path == fixture.download:
            fixture.record('download')
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(fixture.archive.stat().st_size))
            self.end_headers()
            with fixture.archive.open('rb') as stream:
                shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
            fixture.record('download_complete')
        else:
            fixture.record('unexpected')
            self.reply(404)

    def do_POST(self):
        if not self.authenticated():
            return
        fixture = self.server.fixture
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 8 * 1024 * 1024:
            self.reply(413)
            return
        payload = json.loads(self.rfile.read(size))
        if self.path == '/client/v3/config':
            fixture.record('config', payload.get('version', ''))
            policy = {'policy_expires_at': int(time.time()) + 3600, 'tracking': False,
                      'interactions': False, 'field_values': False, 'ai': False,
                      'app_inventory': False, 'domains': [], 'track_processes': []}
            self.reply(200, json.dumps({'ok': True, 'device_id': fixture.device['device_id'],
                                       'config': policy}).encode())
        elif self.path == '/client/v3/events':
            fixture.record('events')
            # Deliberately no server ACK: pending custody must survive the upgrade.
            self.reply(503)
        else:
            fixture.record('unexpected')
            self.reply(404)


class ProxyHandler(QuietHandler):
    def setup(self):
        self.request.settimeout(30)
        super().setup()
        with self.server.socket_lock:
            self.server.sockets.add(self.connection)

    def finish(self):
        try:
            super().finish()
        finally:
            with self.server.socket_lock:
                self.server.sockets.discard(self.connection)

    def do_CONNECT(self):
        if self.path not in tuple(host + ':443' for host in HOSTS):
            self.server.fixture.record('blocked_connect')
            self.reply(403)
            return
        self.send_response(200, 'Connection established')
        self.end_headers()
        self.wfile.flush()
        try:
            with self.server.tls.wrap_socket(self.connection, server_side=True) as connection:
                with self.server.socket_lock:
                    self.server.sockets.add(connection)
                try:
                    OriginHandler(connection, self.client_address, self.server)
                finally:
                    with self.server.socket_lock:
                        self.server.sockets.discard(connection)
        except (ssl.SSLError, ConnectionError, TimeoutError):
            pass

    def do_GET(self):
        self.server.fixture.record('blocked_plaintext')
        self.reply(403)

    do_POST = do_GET


class LocalServer(ThreadingHTTPServer):
    daemon_threads = False

    def handle_error(self, *_args):
        self.fixture.record('server_error')


@contextmanager
def local_server(fixture, tls):
    server = LocalServer(('127.0.0.1', 0), ProxyHandler)
    server.fixture, server.tls = fixture, tls
    server.sockets, server.socket_lock = set(), threading.Lock()
    thread = threading.Thread(target=server.serve_forever, name='auto-update-fixture')
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        fixture.release.set()
        server.shutdown()
        with server.socket_lock:
            for connection in tuple(server.sockets):
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        server.server_close()
        thread.join(timeout=5)
        require(not thread.is_alive(), 'Fixture server did not stop')


def child_environment(directory, root, proxy, cert):
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'DISPLAY', 'XAUTHORITY',
               'LANG', 'LC_ALL', 'DBUS_SESSION_BUS_ADDRESS', 'USER', 'USERNAME', 'LOGNAME',
               'USERPROFILE'}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    for folder in ('home', 'tmp', 'config', 'cache'):
        (directory / folder).mkdir(exist_ok=True)
    env.update(HOME=str(directory / 'home'), TMP=str(directory / 'tmp'), TEMP=str(directory / 'tmp'),
               TMPDIR=str(directory / 'tmp'), APPDATA=str(directory / 'home'),
               LOCALAPPDATA=str(directory / 'home'), XDG_DATA_HOME=str(directory / 'home'),
               XDG_CONFIG_HOME=str(directory / 'config'), XDG_CACHE_HOME=str(directory / 'cache'),
               SOFT_TRACKING_INSTALL=str(root), HTTPS_PROXY=proxy, HTTP_PROXY=proxy, ALL_PROXY=proxy,
               NO_PROXY='localhost,127.0.0.1,::1', REQUESTS_CA_BUNDLE=str(cert),
               SSL_CERT_FILE=str(cert), PYTHONNOUSERSITE='1')
    return env


class ProcessTree:
    def __init__(self, process):
        self.process = process
        self.parent = psutil.Process(process.pid)
        self.known = set()

    def snapshot(self):
        try:
            children = self.parent.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        self.known.update(children)
        return children

    def cleanup(self):
        self.snapshot()
        self.known.add(self.parent)
        for process in self.known:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(self.known, timeout=10)
        for process in alive:
            process.kill()
        _, alive = psutil.wait_procs(alive, timeout=10)
        self.process.wait(timeout=10)
        require(not alive, 'Owned process cleanup failed')


def runtime_ready(children, root, version):
    expected = (root / 'versions' / version / 'app' / executable()).resolve()
    supervisor, runtime = False, None
    for process in children:
        try:
            if Path(process.exe()).resolve() != expected:
                continue
            args = process.cmdline()
            if '--supervisor' in args:
                supervisor = True
            elif '--health-check' not in args:
                env = process.environ()
                marker = Path(env.get('SOFT_TRACKING_HEALTH', '/missing'))
                token = env.get('SOFT_TRACKING_RUN_TOKEN')
                if marker.parent == root / 'run' and marker.stem == token and read_json(marker, {}).get('ready') is True:
                    runtime = marker
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return runtime if supervisor else None


def wait_for(tree, predicate, timeout, message):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(tree.process.poll() is None, message + ': launcher exited')
        result = predicate(tree.snapshot())
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError(message + ': timeout')


def seed_data(directory, ClientState, EventQueue):
    state = ClientState(directory)
    try:
        device = state.get('device')
        identity = dict(device_id=device['device_id'], company_id=101, user_id=202,
                        company_name='Acceptance fixture', user_name='Synthetic employee')
        state.set('identity', identity)
        state.set('company_code_hash', hashlib.sha256(('a' * 32).encode()).hexdigest())
    finally:
        state.close()
    events = [dict(event_id=secrets.token_hex(16), type='navigation', timestamp=1700000000,
                   url='https://example.test/') for _ in range(3)]
    queue = EventQueue(directory / 'outbox.sqlite3')
    try:
        for event in events:
            queue.push_payload(event)
        queue.mark_rejected(events[1]['event_id'], 'policy_disabled')
        queue.acknowledge([events[2]['event_id']])
    finally:
        queue.close()
    return device, identity, events


def assert_data(directory, device, identity, events, ClientState, EventQueue):
    state = ClientState(directory)
    try:
        require(state.get('device') == device and state.get('identity') == identity,
                'Device or employee identity changed')
        require(state.get('employee_epoch') == state.get('legacy_employee_epoch') == device['device_id'],
                'Employee queue ownership changed')
        require(state.get('company_code_hash') == hashlib.sha256(('a' * 32).encode()).hexdigest(),
                'Company binding changed')
        row = state.db.execute('SELECT device,identity,status FROM employee_profiles WHERE epoch=?',
                               (device['device_id'],)).fetchone()
        require(row and json.loads(row[0]) == device and json.loads(row[1]) == identity and row[2] == 'active',
                'Persisted employee profile changed')
    finally:
        state.close()
    queue = EventQueue(directory / 'outbox.sqlite3')
    try:
        pending, rejected, confirmed = events
        require(pending in queue.batch(), 'Pending event lost or rewritten')
        require(queue.rejections([rejected['event_id']]) == {rejected['event_id']: 'policy_disabled'},
                'Rejected event lost its quarantine')
        require(queue.confirmed([event['event_id'] for event in events]) == [confirmed['event_id']],
                'Pending custody became a server ACK')
        with closing(sqlite3.connect((directory / 'outbox.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT payload FROM outbox WHERE event_id=?', (rejected['event_id'],)).fetchone()
            require(row and json.loads(row[0]) == rejected, 'Rejected payload changed')
    finally:
        queue.close()


def main(old, new, timeout=420):
    global STAGE
    STAGE = 'prepare'
    ClientState, EventQueue, atomic_json, signed = client_modules()
    from smoke_helpers import assert_electron_child, assert_ui_health
    with tempfile.TemporaryDirectory(prefix='tracking-auto-update-') as temporary:
        directory = Path(temporary).resolve()
        root = directory / 'install'
        baseline, manifest, envelope, archive = prepare_install(Path(old), Path(new), root, signed, atomic_json)
        old_version = baseline['version']
        version = manifest['version']
        bootstraps = {executable(native, True): digest(root / executable(native, True)) for native in (False, True)}
        keyring = digest(root / 'trusted-update-keys.json')
        device, identity, events = seed_data(directory, ClientState, EventQueue)
        STAGE = 'local-tls'
        cert, tls = make_certificate(directory)
        fixture = Fixture(manifest, envelope, archive, device)
        with local_server(fixture, tls) as proxy, (directory / 'runtime.private.log').open('wb') as log:
            env = child_environment(directory, root, proxy, cert)
            STAGE = 'old-launcher-start'
            process = subprocess.Popen([str(root / executable(bootstrap=True)), '--autostart'], env=env,
                                       cwd=directory, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            tree = ProcessTree(process)
            try:
                STAGE = 'old-runtime-ready'
                old_ready = wait_for(tree, lambda children: runtime_ready(children, root, old_version), 60,
                                     'Original frozen supervisor/runtime not ready')
                assert_ui_health(old_ready, baseline)
                assert_electron_child(tree.snapshot(), root / 'versions' / old_version / 'app', baseline)
                require(read_json(root / 'failed.json')['version'] == old_version, 'Legacy failure fixture missing')
                fixture.release.set()
                transition_deadline = time.monotonic() + timeout
                STAGE = 'automatic-catalog-fetch'
                wait_for(tree, lambda _: fixture.seen('catalog', old_version), timeout,
                         'Original supervisor did not request the catalog')
                STAGE = 'automatic-archive-fetch'
                wait_for(tree, lambda _: fixture.seen('download_complete'),
                         max(0, transition_deadline - time.monotonic()), 'Original supervisor did not download the archive')
                STAGE = 'automatic-transition'

                def upgraded(children):
                    if read_json(root / 'current.json', {}).get('version') != version:
                        return None
                    if read_json(root / 'last-good.json', {}) != {'version': version, 'previous_version': old_version}:
                        return None
                    if (root / 'pending.json').exists():
                        return None
                    return runtime_ready(children, root, version)

                ready = wait_for(tree, upgraded, max(0, transition_deadline - time.monotonic()),
                                 'Automatic update did not confirm and restart')
                STAGE = 'new-runtime-health'
                metadata = read_json(root / 'versions' / version / 'build.json')
                require(metadata['version'] == version and metadata['target'] == manifest['target'], 'Wrong new runtime')
                assert_ui_health(ready, metadata)
                assert_electron_child(tree.snapshot(), root / 'versions' / version / 'app', metadata)
                health_receipts = set((root / 'run').glob('*.ready')) - {old_ready, ready}
                require(bool(health_receipts), 'Candidate health receipt missing')
                for receipt in health_receipts:
                    require(read_json(receipt, {}).get('ready') is True, 'Candidate was not ready')
                    assert_ui_health(receipt, metadata)
                STAGE = 'new-runtime-config'
                wait_for(tree, lambda _: fixture.seen('config', version), 30, 'New runtime did not contact fixture')
                STAGE = 'automatic-fetch-evidence'
                require(fixture.seen('catalog', old_version) and fixture.seen('download_complete'),
                        'Original supervisor did not fetch the release automatically')
                require(not fixture.seen('unauthorized') and not fixture.seen('unexpected')
                        and not fixture.seen('blocked_connect') and not fixture.seen('server_error'),
                        'Unexpected fixture traffic')
                STAGE = 'graceful-shutdown'
                atomic_json(root / 'stop-request.json', {'token': ready.stem})
                require(process.wait(timeout=75) == 0, 'New runtime did not stop gracefully')
                _, alive = psutil.wait_procs(tree.known, timeout=10)
                require(not alive, 'Graceful stop left child processes running')
            finally:
                try:
                    tree.cleanup()
                except Exception:
                    STAGE = 'process-cleanup'
                    raise
        STAGE = 'data-preservation'
        assert_data(directory, device, identity, events, ClientState, EventQueue)
        require(read_json(root / 'current.json') == {'version': version} and not (root / 'pending.json').exists(),
                'New version was not retained after shutdown')
        require(read_json(root / 'enrollment.json') == {'company_code': 'a' * 32}, 'Enrollment changed')
        require(all(digest(root / name) == value for name, value in bootstraps.items()), 'Original launcher replaced')
        require(digest(root / 'trusted-update-keys.json') == keyring, 'Trusted update keys changed')
        require(read_json(root / 'extension/manifest.json') ==
                read_json(root / 'versions' / version / 'extension/manifest.json'), 'Extension did not switch')
    STAGE = 'complete'
    print('PASS: frozen ' + old_version + ' -> ' + version +
          ' automatic HTTPS fetch, signed update, health confirmation, launcher restart, '
          'old failed.json bypass, identity/queue retained; temporary files/processes removed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('old', type=Path)
    parser.add_argument('new', type=Path)
    parser.add_argument('--timeout', type=int, default=420, help='Automatic transition deadline in seconds')
    args = parser.parse_args()
    try:
        require(1 <= args.timeout <= 900, 'Timeout must be between 1 and 900 seconds')
        main(args.old, args.new, args.timeout)
    except Exception as error:
        # Exception messages from TLS/HTTP/native code may contain private data.
        print('FAIL: automatic-update stage=' + STAGE + ' exception=' + type(error).__name__, flush=True)
        raise SystemExit(1)
