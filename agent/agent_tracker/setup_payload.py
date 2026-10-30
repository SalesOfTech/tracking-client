"""Locate either the native macOS installer payload or the frozen legacy payload."""
import os
import sys
from pathlib import Path


def setup_payload():
    external = os.environ.get('SOFT_TRACKING_SETUP_PAYLOAD')
    if external and sys.platform == 'darwin':
        expected = Path(sys.executable).parent.parent / 'Resources' / 'setup-payload'
        candidate = Path(external)
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
            raise ValueError('Invalid native setup payload')
        # macOS exposes the same temporary directory through /var and /private/var.
        # The native wrapper canonicalizes its path; Foundation may keep the alias.
        if candidate.resolve(strict=True) != expected.resolve(strict=True):
            raise ValueError('Invalid native setup payload')
        return candidate.resolve(strict=True)
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'setup-payload'
