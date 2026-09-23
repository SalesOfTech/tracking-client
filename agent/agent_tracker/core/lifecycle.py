"""Durable availability evidence, separate from collected employee activity."""
import json
import time
import uuid

from .version import application_version

KINDS = frozenset(('started', 'heartbeat', 'stop_requested', 'uninstall_requested'))


def _ensure(state):
    state.db.execute('''CREATE TABLE IF NOT EXISTS lifecycle_outbox (
        id TEXT PRIMARY KEY, epoch TEXT NOT NULL, payload TEXT NOT NULL)''')


def record(client, kind, now=None):
    if kind not in KINDS:
        raise ValueError('Invalid lifecycle event')
    profile = client.profiles.active()
    if not profile.get('identity'):
        return None
    event = {'id': uuid.uuid4().hex, 'kind': kind,
             'timestamp': int(time.time() if now is None else now),
             'version': application_version()}
    with client.state.lock, client.state.db:
        _ensure(client.state)
        client.state.db.execute('INSERT INTO lifecycle_outbox VALUES(?,?,?)',
                                (event['id'], profile['epoch'], json.dumps(event)))
    return event['id']


def flush(client, active_only=False):
    """Delete only explicit acknowledgements, using the original employee identity."""
    failures = []
    profiles = [client.profiles.active()] if active_only else client.profiles.delivery_profiles()
    # Active employees must not wait behind a revoked retired identity.
    profiles.sort(key=lambda value: value['epoch'] != client.employee_epoch)
    for profile in profiles:
        if not profile.get('identity'):
            continue
        with client.state.lock:
            _ensure(client.state)
            rows = client.state.db.execute(
                'SELECT id,payload FROM lifecycle_outbox WHERE epoch=? ORDER BY rowid ' +
                ('DESC' if active_only else 'ASC') + ' LIMIT 100',
                (profile['epoch'],)).fetchall()
        if not rows:
            continue
        try:
            result = client._post(profile, '/client/v3/lifecycle',
                                  {'events': [json.loads(row[1]) for row in rows]})
            accepted = result.get('accepted') if isinstance(result, dict) else None
            sent = {row[0] for row in rows}
            if (not isinstance(result, dict) or result.get('ok') is not True or not isinstance(accepted, list) or
                    any(not isinstance(value, str) or value not in sent for value in accepted) or
                    len(accepted) != len(set(accepted))):
                raise ValueError('Invalid lifecycle acknowledgement')
        except Exception as error:
            failures.append(error)
            continue
        with client.state.lock, client.state.db:
            client.state.db.executemany('DELETE FROM lifecycle_outbox WHERE id=? AND epoch=?',
                                       [(value, profile['epoch']) for value in accepted])
    if failures:
        raise failures[0]


class Reporter:
    def __init__(self, client):
        self.client = client
        self.epoch = None
        self.next_record = 0
        self.next_flush = 0
        self.stop_recorded = False

    def observe(self):
        if self.stop_recorded or not self.client.state.get('identity'):
            return
        now = time.monotonic()
        epoch = self.client.employee_epoch
        if epoch != self.epoch:
            record(self.client, 'started')
            self.epoch = epoch
            self.next_record = now + 60
            self.next_flush = 0
        elif now >= self.next_record:
            # Do not invent heartbeats for time spent asleep or disconnected.
            record(self.client, 'heartbeat')
            self.next_record = now + 60
    def deliver(self, final=False):
        now = time.monotonic()
        if final or now >= self.next_flush:
            self.next_flush = now + 60
            try:
                flush(self.client, active_only=final)
                self.client.state.set('lifecycle_error', '')
            except Exception:
                self.client.state.set('lifecycle_error', 'availability_delivery_pending')

    def tick(self):
        self.observe()
        self.deliver()

    def stopping(self):
        if self.stop_recorded:
            return
        record(self.client, 'stop_requested')
        self.stop_recorded = True
