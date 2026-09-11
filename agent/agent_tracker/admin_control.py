"""Controller-facing cooperative stop authorization, with no persistent grants."""
from __future__ import annotations

from pathlib import Path
import threading

from . import admin_platform
from .core.release_manager import executable_name
from .integration import autostart_status


class AdminControl:
    def __init__(self, install=None):
        self.install = Path(install) if install is not None else None
        self._request_lock = threading.Lock()

    def status(self):
        return {
            'scope': 'per_user',
            'admin_required': True,
            'force_kill_protected': False,
            'authorization': admin_platform.authorization_status(),
            'request_pending': self._request_lock.locked(),
            'autostart': autostart_status(self.install / executable_name(False, True))
            if self.install is not None else {'registered': False, 'scope': 'user_login',
                                             'effective': 'unknown', 'error': 'not_installed'},
        }

    def request_stop(self):
        """Call off the UI thread; only this call's authorized=True permits stop.

        The controller must use its existing graceful worker shutdown and leave
        the durable queue intact. No grant is cached, persisted or put in status.
        """
        if not self._request_lock.acquire(blocking=False):
            return {'authorized': False, 'state': 'busy'}
        try:
            try:
                state = admin_platform.authorize_stop()
            except Exception:
                state = 'error'
            if state not in ('authorized', 'cancelled', 'denied', 'timed_out', 'unavailable', 'error'):
                state = 'error'
            return {'authorized': state == 'authorized', 'state': state}
        finally:
            self._request_lock.release()
