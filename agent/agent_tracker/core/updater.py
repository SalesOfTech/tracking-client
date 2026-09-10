from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from .config_manager import UpdateInfo
from .networking import HttpClient
from .storage import StorageManager


LOG = logging.getLogger(__name__)


class UpdateManager:
    def __init__(self, storage: StorageManager, http_client: HttpClient) -> None:
        self.storage = storage
        self.http_client = http_client

    def maybe_update(self, update_info: Optional[UpdateInfo], current_version: str) -> bool:
        if not update_info or not update_info.has_update:
            return False
        if update_info.version <= current_version:
            return False
        LOG.info("Update available: %s -> %s", current_version, update_info.version)
        temp_path = self._download_update(update_info)
        self._swap_binary(temp_path)
        self._launch_new_binary()
        return True

    def _download_update(self, update: UpdateInfo) -> Path:
        response = self.http_client.session.get(update.download_url, stream=True, timeout=30)
        response.raise_for_status()
        fd, path = tempfile.mkstemp(prefix="soft-agent-", suffix=".bin")
        sha256 = hashlib.sha256()
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    handle.write(chunk)
                    sha256.update(chunk)
        checksum = f"sha256:{sha256.hexdigest()}"
        if checksum.lower() != update.checksum.lower():
            raise RuntimeError("Checksum mismatch for downloaded update")
        return Path(path)

    def _swap_binary(self, new_binary: Path) -> None:
        target = self.storage.binary_path
        backup = target.with_suffix(".bak")
        LOG.info("Replacing %s with %s", target, new_binary)
        if target.exists():
            shutil.move(target, backup)
        shutil.move(new_binary, target)
        if os.name != "nt":
            target.chmod(0o755)
        if backup.exists():
            backup.unlink(missing_ok=True)

    def _launch_new_binary(self) -> None:
        binary = self.storage.binary_path
        LOG.info("Launching updated agent: %s", binary)
        subprocess.Popen([str(binary)], close_fds=True)
