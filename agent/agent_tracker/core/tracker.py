from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Set

from .event_queue import Event, EventQueue, current_timestamp


LOG = logging.getLogger(__name__)


@dataclass
class TrackerConfig:
    poll_ms: int = 1000
    inactivity_ms: int = 30000
    track_processes: Sequence[str] = ()


@dataclass
class SessionState:
    identifier: str
    display_name: str
    started_at: int


class ActivityTracker(threading.Thread):
    daemon = True
    MAX_SESSION_SECONDS = 60

    def __init__(
        self,
        platform_adapter,
        event_queue: EventQueue,
        tracker_config: TrackerConfig,
    ) -> None:
        super().__init__(name="ActivityTracker")
        self.platform = platform_adapter
        self.events = event_queue
        self.cfg = tracker_config
        self._stop_event = threading.Event()
        self._session: Optional[SessionState] = None
        self._pending_event: Optional[Event] = None
        self.last_error = ""
        self._last_observation = None
        self._last_poll_clock = None
        self._tracked: Set[str] = set(process.lower() for process in self.cfg.track_processes)

    def update_config(self, tracker_config: TrackerConfig) -> None:
        LOG.info("Tracker config updated: %s", tracker_config)
        self.cfg = tracker_config
        self._tracked = set(process.lower() for process in self.cfg.track_processes)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                self.last_error = "Application collection paused: local storage or platform error"
                LOG.exception("Tracker loop failure: %s", exc)
            poll_seconds = max(self.cfg.poll_ms, 250) / 1000.0
            time.sleep(poll_seconds)
        self._close_session(reason="shutdown")

    def _poll_once(self) -> None:
        if self._pending_event is not None:
            self.events.push(self._pending_event)
            self._pending_event = None
        self.last_error = ""
        if not self._tracked:
            self._close_session(reason="disabled")
            return
        now, clock = current_timestamp(), time.monotonic()
        gap_limit = max(5, self.cfg.poll_ms / 1000.0 * 3)
        if self._last_poll_clock is not None and (
                clock - self._last_poll_clock > gap_limit or now < self._last_observation or
                abs((now - self._last_observation) - (clock - self._last_poll_clock)) > gap_limit):
            self._close_session("observation_gap", end_at=self._last_observation)
        self._last_poll_clock = clock
        try:
            if not self.platform.is_session_active():
                self._close_session("session_inactive", end_at=self._last_observation)
                self._last_observation = now
                return
            idle = self.platform.get_idle_duration_ms()
            if idle < 0:
                raise OSError("Invalid idle duration")
            active = self.platform.get_active_application() if idle < self.cfg.inactivity_ms else None
        except Exception:
            self.last_error = "Application collection paused: Windows session state unavailable"
            self._close_session("platform_unavailable", end_at=self._last_observation)
            self._last_observation = now
            return
        self._last_observation = now
        if idle >= self.cfg.inactivity_ms:
            cutoff = now - max(0, idle - self.cfg.inactivity_ms) // 1000
            self._close_session(reason="idle", end_at=cutoff)
            return

        if not active:
            self._close_session(reason="no_active_app")
            return

        match = self._match_tracked_application(active)
        if not match:
            if self._session:
                LOG.debug("Closing session for %s: active app %s not tracked", self._session.display_name, self._describe_active(active))
                self._close_session(reason="not_whitelisted")
            else:
                LOG.debug("Ignoring active app %s (not tracked)", self._describe_active(active))
            return

        identifier, display_name = match
        if not self._session:
            self._start_session(identifier, display_name)
            return
        if self._session.identifier != identifier:
            self._close_session(reason="switched_app")
            self._start_session(identifier, display_name)
            return
        self._maybe_split_session(identifier, display_name)

    def _match_tracked_application(self, active) -> Optional[tuple[str, str]]:
        candidates = []
        if active.process_name:
            candidates.append(active.process_name.lower())
        if active.executable:
            executable = active.executable.lower()
            candidates.append(executable)
            try:
                name = Path(active.executable).name.lower()
                stem = Path(active.executable).stem.lower()
                candidates.extend([name, stem])
            except Exception:
                pass
        if active.bundle_id:
            candidates.append(active.bundle_id.lower())

        for candidate in candidates:
            if candidate in self._tracked:
                return candidate, self._display_name(active, candidate)
        return None

    @staticmethod
    def _display_name(active, fallback: str) -> str:
        if active.process_name:
            return active.process_name
        if active.executable:
            try:
                return Path(active.executable).name
            except Exception:
                return active.executable
        if active.bundle_id:
            return active.bundle_id
        return fallback

    @staticmethod
    def _describe_active(active) -> str:
        parts = []
        if active.process_name:
            parts.append(active.process_name)
        if active.bundle_id:
            parts.append(active.bundle_id)
        if active.executable:
            parts.append(active.executable)
        if not parts:
            return "<unknown>"
        return " | ".join(parts)

    def _start_session(self, identifier: str, display_name: str) -> None:
        now = current_timestamp()
        LOG.debug("start session for %s", display_name)
        self._session = SessionState(identifier=identifier, display_name=display_name, started_at=now)

    def _close_session(self, reason: str, end_at=None) -> None:
        if not self._session:
            return
        now = max(self._session.started_at, current_timestamp() if end_at is None else end_at)
        duration = max(0, now - self._session.started_at)
        LOG.debug("end session for %s reason=%s duration=%s", self._session.display_name, reason, duration)
        event = Event(
                type="session",
                timestamp=self._session.started_at,
                end_timestamp=now,
                exe=self._session.display_name,
                application_id=self._session.identifier,
                duration_sec=duration,
                reason=reason,
        )
        self._session = None
        self._pending_event = event
        self.events.push(event)
        self._pending_event = None

    def _maybe_split_session(self, identifier: str, display_name: str) -> None:
        if not self._session:
            return
        now = current_timestamp()
        duration = now - self._session.started_at
        if duration < self.MAX_SESSION_SECONDS:
            return
        LOG.debug("Splitting long session for %s after %s seconds", display_name, duration)
        self._close_session(reason="duration_limit")
        self._start_session(identifier, display_name)
