from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


INSTALL_ID_FILENAME = "install_id.txt"
CONFIG_CACHE_FILENAME = "config.json"
LOG_FILENAME = "agent.log"
PID_FILENAME = "agent.pid"


@dataclass
class InstallMetadata:
    install_id: str
    workspace: Path
    binary_path: Path


class StorageManager:
    """Handles workspace prep and knowledge about the running binary."""

    def __init__(self, app_name: str = "SOFT.Agent", company: str = "SOFT") -> None:
        self.app_name = app_name
        self.company = company
        self._workspace = self._resolve_workspace_path()
        self._executable_path = self._resolve_executable_path()

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def binary_path(self) -> Path:
        return self._executable_path

    @property
    def install_id_path(self) -> Path:
        return self.workspace / INSTALL_ID_FILENAME

    @property
    def config_cache_path(self) -> Path:
        return self.workspace / CONFIG_CACHE_FILENAME

    @property
    def log_path(self) -> Path:
        if getattr(sys, "frozen", False):
            return self.binary_path.with_suffix(".log")
        return self.workspace / LOG_FILENAME

    @property
    def pid_path(self) -> Path:
        return self.workspace / PID_FILENAME

    def _resolve_workspace_path(self) -> Path:
        if sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
            return base / "SOFT" / "Agent"
        return Path.home() / "Library" / "Application Support" / "SOFT" / "Agent"

    def ensure_workspace(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)

    def load_or_create_install_id(self) -> str:
        if self.install_id_path.exists():
            return self.install_id_path.read_text(encoding="utf-8").strip()
        install_id = str(uuid.uuid4())
        self.install_id_path.write_text(install_id, encoding="utf-8")
        return install_id

    def _resolve_executable_path(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve()
        # running from sources
        return Path(sys.argv[0]).resolve()

    def load_cached_config(self) -> Optional[dict]:
        if not self.config_cache_path.exists():
            return None
        try:
            return json.loads(self.config_cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def save_cached_config(self, config: dict) -> None:
        self.config_cache_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def read_existing_pid(self) -> Optional[int]:
        if not self.pid_path.exists():
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def write_pid(self, pid: int) -> None:
        self.pid_path.write_text(str(pid), encoding="utf-8")

    def clear_pid(self) -> None:
        try:
            self.pid_path.unlink()
        except FileNotFoundError:
            pass
