"""Current-user controls. Stopping never requires OS administrator approval."""
from __future__ import annotations

from pathlib import Path
import threading

from .core.release_manager import executable_name
from .integration import autostart_status


class AdminControl:
    def __init__(self, install=None):
        self.install = Path(install) if install is not None else None
        self._request_lock = threading.Lock()

    def status(self):
        return {
            'scope': 'per_user',
            'admin_required': False,
            'force_kill_protected': False,
            'authorization': {'available': True, 'mechanism': 'current_user'},
            'request_pending': self._request_lock.locked(),
            'autostart': autostart_status(self.install / executable_name(False, True))
            if self.install is not None else {'registered': False, 'scope': 'user_login',
                                             'effective': 'unknown', 'error': 'not_installed'},
        }

    def request_stop(self):
        """Accept an ordinary confirmed stop without elevation.

        The controller still checkpoints active sessions and preserves the queue
        before exit. Keeping this result shape supports both desktop interfaces.
        """
        if not self._request_lock.acquire(blocking=False):
            return {'authorized': False, 'state': 'busy'}
        try:
            return {'authorized': True, 'state': 'authorized'}
        finally:
            self._request_lock.release()
