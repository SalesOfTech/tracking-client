from __future__ import annotations

import argparse
import atexit
import getpass
import logging
import os
import platform
import signal
import sys
import threading
import time
from importlib import resources
from pathlib import Path

import psutil
from PIL import Image

from agent_tracker import __version__
from agent_tracker.core.config_manager import ConfigManager
from agent_tracker.core.dispatcher import EventDispatcher
from agent_tracker.core.event_queue import EventQueue
from agent_tracker.core.networking import HttpClient
from agent_tracker.core.runtime_config import RuntimeConfig, load_runtime_config
from agent_tracker.core.storage import StorageManager, LOG_FILENAME
from agent_tracker.core.tracker import ActivityTracker, TrackerConfig
from agent_tracker.core.updater import UpdateManager
from agent_tracker.platform import load_platform_adapter
from agent_tracker.ui.tray import TrayAction, TrayController


LOG = logging.getLogger("agent")


class AgentApplication:
    def __init__(
        self,
        runtime_config: RuntimeConfig,
        no_autostart: bool = False,
        storage: StorageManager | None = None,
    ) -> None:
        self.runtime_config = runtime_config
        self.base_url = runtime_config.base_url
        self.no_autostart = no_autostart
        self.storage = storage or StorageManager()
        self.storage.ensure_workspace()
        self.install_id = self.storage.load_or_create_install_id()
        self.platform = load_platform_adapter()
        self.http = HttpClient(base_url=self.base_url)
        self.config_manager = ConfigManager(self.storage, self.http, config_path=runtime_config.config_endpoint)
        self.event_queue = EventQueue(self.storage.workspace / "outbox.sqlite3")
        self.tracker = ActivityTracker(self.platform, self.event_queue, TrackerConfig())
        self.dispatcher = EventDispatcher(
            self.event_queue,
            self.http,
            self.storage,
            flush_seconds=30,
            envelope=self._base_envelope(),
            endpoint=runtime_config.events_endpoint,
        )
        self.update_manager = UpdateManager(self.storage, self.http)
        self._stop_event = threading.Event()
        self._config_thread = threading.Thread(target=self._config_loop, name="ConfigLoop", daemon=True)
        self._tray: TrayController | None = None
        self._autostart_enabled = not self.no_autostart
        self._pid_registered = False

    def _base_envelope(self) -> dict:
        return {
            "os": self.platform.platform_name(),
            "username": getpass.getuser(),
            "machine": platform.node(),
            "install_id": self.install_id,
            "version": __version__,
            "company_id": self.runtime_config.company_id,
        }

    def prepare(self) -> None:
        LOG.info("Preparing agent workspace at %s", self.storage.workspace)
        self._enforce_single_instance()
        if self._autostart_enabled:
            self.platform.ensure_autostart(str(self.storage.binary_path), enable=True)

        cached = self.config_manager.load_cached()
        if cached:
            self.tracker.update_config(cached.to_tracker_config())
            self.dispatcher.update_interval(cached.flush_seconds)

    def run(self) -> None:
        LOG.info("Starting agent version %s", __version__)
        self.dispatcher.start()
        self.tracker.start()
        self._config_thread.start()
        self._install_signal_handlers()
        tray_blocks = self.platform.platform_name() == "macos" and TrayController.is_supported()
        tray_started = self._start_tray(block=tray_blocks)
        if tray_blocks:
            if not tray_started:
                self._wait_for_shutdown()
            return
        self._wait_for_shutdown()

    def _start_tray(self, block: bool = False) -> bool:
        actions = [
            TrayAction(label="Toggle Autostart", callback=self.toggle_autostart),
            TrayAction(label="Quit", callback=self.stop),
        ]
        light_icon, dark_icon = self._load_tray_icons()
        self._tray = TrayController("SOFT Agent Tracking", actions, icon_light=light_icon, icon_dark=dark_icon)
        try:
            return self._tray.start(block=block)
        except Exception as exc:
            LOG.exception("Unable to start tray icon: %s", exc)
            return False

    def _load_tray_icons(self) -> tuple[Image.Image | None, Image.Image | None]:
        def load_from_package(filename: str) -> Image.Image | None:
            try:
                icon_path = resources.files("agent_tracker.assets").joinpath(filename)
                with icon_path.open("rb") as stream:
                    image = Image.open(stream)
                    image.load()
                    return image.convert("RGBA")
            except Exception as exc:
                LOG.warning("Cannot load embedded tray icon %s: %s", filename, exc)
                return None

        return load_from_package("tray_light.png"), load_from_package("tray_dark.png")

    def toggle_autostart(self) -> None:
        self._autostart_enabled = not self._autostart_enabled
        if self._autostart_enabled:
            self.platform.ensure_autostart(str(self.storage.binary_path), enable=True)
            LOG.info("Autostart enabled")
        else:
            self.platform.remove_autostart()
            LOG.info("Autostart disabled")

    def stop(self) -> None:
        LOG.info("Stopping agent")
        self._stop_event.set()
        self.tracker.stop()
        self.dispatcher.stop()
        if self._tray:
            self._tray.stop()
        if self._pid_registered:
            self.storage.clear_pid()
            self._pid_registered = False

    def _install_signal_handlers(self) -> None:
        def handle_signal(signum, frame):
            LOG.info("Signal %s received, stopping", signum)
            self.stop()

        signal.signal(signal.SIGTERM, handle_signal)
        if sys.platform != "win32":
            signal.signal(signal.SIGINT, handle_signal)

    def _wait_for_shutdown(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.5)

    def _config_loop(self) -> None:
        payload = self._base_envelope()
        while not self._stop_event.is_set():
            try:
                config, update = self.config_manager.fetch_remote(payload)
                self.tracker.update_config(config.to_tracker_config())
                self.dispatcher.update_interval(config.flush_seconds)
                if update.has_update:
                    LOG.warning("Legacy unsigned updater disabled; use the v3 signed release workflow")
            except Exception as exc:
                LOG.error("Config refresh failed: %s", exc)
            finally:
                for _ in range(60 * 60):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

    def _enforce_single_instance(self) -> None:
        existing_pid = self.storage.read_existing_pid()
        current_pid = os.getpid()
        if existing_pid and existing_pid != current_pid and self._process_alive(existing_pid):
            LOG.info("Existing agent instance detected (pid=%s), terminating it", existing_pid)
            self._terminate_process(existing_pid)
            self._wait_for_exit(existing_pid)
        self.storage.write_pid(current_pid)
        self._pid_registered = True
        atexit.register(self._cleanup_pid_file)

    def _cleanup_pid_file(self) -> None:
        if self._pid_registered:
            self.storage.clear_pid()
            self._pid_registered = False

    @staticmethod
    def _process_alive(pid: int) -> bool:
        try:
            proc = psutil.Process(pid)
            return proc.is_running()
        except psutil.Error:
            return False

    @staticmethod
    def _terminate_process(pid: int) -> None:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
        except psutil.NoSuchProcess:
            return
        except psutil.AccessDenied:
            LOG.warning("No permission to terminate pid %s", pid)

    @staticmethod
    def _wait_for_exit(pid: int, timeout: float = 10.0) -> None:
        start = time.time()
        while time.time() - start < timeout:
            if not AgentApplication._process_alive(pid):
                return
            time.sleep(0.2)
        # force kill if still alive
        try:
            proc = psutil.Process(pid)
            proc.kill()
        except psutil.Error:
            pass


def configure_logging(debug: bool = False, log_path: str | Path | None = None) -> None:
    log_level = logging.DEBUG if debug else logging.INFO
    handlers: list[logging.Handler] = []
    file_handler = _create_file_handler(Path(log_path) if log_path else None)
    if file_handler:
        handlers.append(file_handler)
    if debug or not handlers:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )
    _install_exception_hooks()


def _create_file_handler(path: Path | None = None) -> logging.Handler | None:
    log_path = path or _resolve_log_path()
    if not log_path:
        return None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return logging.FileHandler(log_path, encoding="utf-8")
    except OSError:
        return None


def _resolve_log_path() -> Path | None:
    candidates: list[Path] = []
    storage: StorageManager | None = None
    binary_path: Path | None = None
    try:
        storage = StorageManager()
        binary_path = storage.binary_path
    except Exception:
        pass

    if binary_path:
        bundle_log = _bundle_log_path(binary_path)
        if bundle_log:
            candidates.append(bundle_log)
        candidates.append(binary_path.with_suffix(".log"))

    if storage:
        try:
            storage.ensure_workspace()
            candidates.append(storage.workspace / LOG_FILENAME)
        except Exception:
            pass

    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
            return path
        except OSError:
            continue
    return None


def _bundle_log_path(binary_path: Path) -> Path | None:
    for parent in binary_path.parents:
        if parent.suffix.lower() == ".app":
            target_dir = parent.parent
            filename = f"{parent.stem}.log"
            return target_dir / filename
    return None


def _install_exception_hooks() -> None:
    def _log_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        LOG.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = _log_exception

    if hasattr(threading, "excepthook"):
        def _thread_hook(args: threading.ExceptHookArgs) -> None:  # type: ignore[attr-defined]
            _log_exception(args.exc_type, args.exc_value, args.exc_traceback)

        threading.excepthook = _thread_hook  # type: ignore[attr-defined]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SOFT Agent Tracker")
    parser.add_argument("--no-autostart", action="store_true", help="Do not register the agent in autostart")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--config", default=None, help="Path to agent_config.json (defaults to ./agent_config.json)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    storage = StorageManager()
    storage.ensure_workspace()
    configure_logging(args.debug, log_path=storage.log_path)
    runtime_cfg = load_runtime_config(args.config)
    app = AgentApplication(runtime_config=runtime_cfg, no_autostart=args.no_autostart, storage=storage)
    app.prepare()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
