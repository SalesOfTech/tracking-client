from __future__ import annotations

import re
import sys
from pathlib import Path

from .. import __version__
from .files import read_json


def application_version():
    # Read the running payload, not current.json, which can switch during an update.
    if getattr(sys, "frozen", False):
        build = read_json(Path(sys.executable).resolve().parent.parent / "build.json", {})
        value = build.get("version", "")
        if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:alpha|beta|rc)\.\d+)?", value):
            return value
    return __version__
