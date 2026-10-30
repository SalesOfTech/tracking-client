"""Updater support reports use the durable, employee-bound diagnostic outbox."""
import time
from contextlib import suppress
from pathlib import Path

from .client import Client
from .files import read_json
from .networking import HttpClient
from .release_manager import retry_at


def report_failure(root, stage, version='unknown', error=None):
    client = None
    try:
        root = Path(root)
        failure = read_json(root / 'failed.json', {})
        matching = failure.get('version') == version
        details = {'update_stage': stage, 'target_version': version,
                   'attempt': failure.get('attempts', 0) if matching else 0,
                   'retry_at': retry_at(failure) if matching else int(time.time()) + 300}
        client = Client(root.parent)
        return client.report_diagnostic('update', error, update=details)
    except Exception:
        # Disk-full or damaged diagnostic state must never prevent rollback.
        return None
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
            with suppress(Exception):
                client.http.session.close()


def flush_reports(root):
    client = None
    try:
        client = Client(Path(root).parent, HttpClient(Client.BASE_URL, timeout=3))
        client.flush_diagnostics()
    except Exception:
        pass
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()
            with suppress(Exception):
                client.http.session.close()
