from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .networking import HttpClient
from .storage import StorageManager
from .tracker import TrackerConfig


LOG = logging.getLogger(__name__)


@dataclass
class UpdateInfo:
    has_update: bool
    version: str
    download_url: str
    checksum: str


@dataclass
class RemoteConfig:
    track_processes: list[str]
    flush_seconds: int
    poll_ms: int
    inactivity_ms: int
    debug: bool = False

    def to_tracker_config(self) -> TrackerConfig:
        return TrackerConfig(
            poll_ms=self.poll_ms,
            inactivity_ms=self.inactivity_ms,
            track_processes=self.track_processes,
        )


class ConfigManager:
    def __init__(
        self,
        storage: StorageManager,
        http_client: HttpClient,
        config_path: str = "/soft-agent/config",
    ) -> None:
        self.storage = storage
        self.http_client = http_client
        self.config_path = config_path
        self._lock = threading.Lock()
        self._config: Optional[RemoteConfig] = None
        self._update: Optional[UpdateInfo] = None

    def load_cached(self) -> Optional[RemoteConfig]:
        cached = self.storage.load_cached_config()
        if not cached:
            return None
        config = RemoteConfig(**cached["config"])
        self._config = config
        if "update" in cached and cached["update"]:
            self._update = UpdateInfo(**cached["update"])
        return config

    def fetch_remote(self, payload: Dict[str, Any]) -> tuple[RemoteConfig, UpdateInfo]:
        response = self.http_client.post_json(self.config_path, payload)
        if not response.get("ok"):
            raise RuntimeError("Server returned unsuccessful response")

        config = RemoteConfig(**response["config"])
        update = UpdateInfo(**response["update"])
        with self._lock:
            self._config = config
            self._update = update
        self.storage.save_cached_config({"config": response["config"], "update": response["update"]})
        return config, update

    def current_config(self) -> Optional[RemoteConfig]:
        with self._lock:
            return self._config

    def update_info(self) -> Optional[UpdateInfo]:
        with self._lock:
            return self._update
