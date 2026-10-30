"""Locate either the native macOS installer payload or the frozen legacy payload."""
import os
import sys
from pathlib import Path


def setup_payload():
    external = os.environ.get('SOFT_TRACKING_SETUP_PAYLOAD')
    if external and sys.platform == 'darwin':
        expected = Path(sys.executable).parent.parent / 'Resources' / 'setup-payload'
        candidate = Path(external)
        if (not candidate.is_absolute() or candidate.is_symlink()
                or not candidate.is_dir() or candidate != expected):
            raise ValueError('Invalid native setup payload')
        return candidate
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload'
