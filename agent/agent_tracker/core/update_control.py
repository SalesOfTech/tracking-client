"""Local update requests; only the supervisor downloads and activates releases."""
import re
import time
import uuid
from pathlib import Path

from .files import atomic_json, read_json

REQUEST = 'update-request.json'
REQUEST_TIMEOUT = 120
STATES = {'active', 'installed', 'checking', 'registration', 'downloading', 'rolled_back', 'error'}


def _read(path):
    try:
        value = read_json(path, {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _request(root):
    value = _read(Path(root) / REQUEST)
    if (set(value) != {'id', 'requested_at'} or
            not isinstance(value.get('id'), str) or not re.fullmatch('[a-f0-9]{32}', value['id']) or
            type(value.get('requested_at')) is not int):
        return {}
    return value


def update_status(root):
    if not root:
        return {'state': 'registration', 'busy': False, 'checked_at': None}
    root = Path(root)
    value = _read(root / 'update-status.json')
    state = value.get('state') if value.get('state') in STATES else 'checking'
    now = time.time()
    request = _request(root)
    pending = bool(request and request['id'] != value.get('request_id'))
    fresh = pending and -30 <= now - request['requested_at'] < REQUEST_TIMEOUT
    started = value.get('started_at')
    timed_out = state == 'checking' and type(started) is int and now - started >= REQUEST_TIMEOUT
    if state != 'downloading':
        if fresh:
            state = 'checking'
        elif pending or timed_out:
            state = 'error'
    busy = state == 'downloading' or bool(fresh) or (state == 'checking' and type(started) is int and not timed_out)
    checked = value.get('checked_at')
    return {'state': state, 'busy': busy, 'checked_at': checked if type(checked) is int else None}


def request_check(root):
    root = Path(root)
    if not (root / 'current.json').is_file() or (root / 'uninstall-requested.json').exists():
        raise ValueError('update_unavailable')
    if update_status(root)['busy']:
        return False
    atomic_json(root / REQUEST, {'id': uuid.uuid4().hex, 'requested_at': int(time.time())})
    return True


def take_request(root):
    root = Path(root)
    request = _request(root)
    if not request or time.time() - request['requested_at'] < -30:
        return None
    (root / REQUEST).unlink(missing_ok=True)
    return request['id']


def publish_status(root, state, request_id=None, **fields):
    value = dict(fields, state=state, request_id=request_id)
    value['started_at' if state in ('checking', 'downloading') else 'checked_at'] = int(time.time())
    atomic_json(Path(root) / 'update-status.json', value)
