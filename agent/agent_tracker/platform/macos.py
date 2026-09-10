from __future__ import annotations

import logging
import plistlib
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from Quartz import (
    CGEventSourceSecondsSinceLastEventType,
    CGWindowListCopyWindowInfo,
    kCGAnyInputEventType,
    kCGEventSourceStateCombinedSessionState,
    kCGNullWindowID,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
)

from .base import ActiveApplication, PlatformAdapter


LOG = logging.getLogger(__name__)


class MacOSPlatform(PlatformAdapter):
    def __init__(self) -> None:
        self._bundle_cache: Dict[str, Optional[str]] = {}
        self._workspace_warning_emitted = False

    def platform_name(self) -> str:
        return "macos"

    def get_active_application(self) -> Optional[ActiveApplication]:
        window = self._frontmost_window()
        if window:
            pid = int(window.get("kCGWindowOwnerPID", 0))
            if pid <= 0:
                return None
            process_name, executable = self._process_metadata(pid)
            bundle_id = self._bundle_identifier(executable)
            owner_name = window.get("kCGWindowOwnerName")
            title = window.get("kCGWindowName")
            return ActiveApplication(
                pid=pid,
                process_name=process_name or owner_name,
                bundle_id=bundle_id,
                title=title,
                executable=executable,
            )

        fallback = self._workspace_app_info()
        if not fallback:
            return None

        pid = fallback["pid"]
        process_name, executable = self._process_metadata(pid)
        bundle_id = fallback.get("bundle_id") or self._bundle_identifier(executable)
        display_name = process_name or fallback.get("name") or bundle_id or "Unknown"

        return ActiveApplication(
            pid=pid,
            process_name=display_name,
            bundle_id=bundle_id,
            title=None,
            executable=executable,
        )

    def get_idle_duration_ms(self) -> int:
        seconds = CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateCombinedSessionState, kCGAnyInputEventType
        )
        return int(seconds * 1000)

    def ensure_autostart(self, binary_path: str, enable: bool = True) -> None:
        agents_dir = Path.home() / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        plist_path = agents_dir / "com.soft.agenttracker.plist"
        if not enable:
            if plist_path.exists():
                plist_path.unlink()
            return

        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.soft.agenttracker</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
"""
        plist_path.write_text(plist, encoding="utf-8")
        LOG.info("LaunchAgent written to %s", plist_path)

    def remove_autostart(self) -> None:
        self.ensure_autostart("", enable=False)

    def _frontmost_window(self) -> Optional[Dict[str, Any]]:
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        try:
            windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
        except Exception as exc:  # pragma: no cover - requires macOS APIs
            LOG.error("Unable to query active application: %s", exc)
            return None
        if not windows:
            return None
        for window in windows:
            layer = int(window.get("kCGWindowLayer", 0))
            if layer == 0:
                return window
        return windows[0]

    def _process_metadata(self, pid: int) -> tuple[Optional[str], Optional[str]]:
        try:
            proc = psutil.Process(pid)
            return proc.name(), proc.exe()
        except psutil.Error as exc:
            LOG.debug("Cannot inspect process %s: %s", pid, exc)
            return None, None

    def _bundle_identifier(self, executable: Optional[str]) -> Optional[str]:
        if not executable:
            return None
        app_dir = self._locate_app_bundle(Path(executable))
        if not app_dir:
            return None
        cache_key = str(app_dir)
        if cache_key in self._bundle_cache:
            return self._bundle_cache[cache_key]

        plist_path = app_dir / "Contents" / "Info.plist"
        bundle_id: Optional[str] = None
        if plist_path.exists():
            try:
                with plist_path.open("rb") as stream:
                    info = plistlib.load(stream)
                bundle_id = info.get("CFBundleIdentifier")
            except Exception as exc:  # pragma: no cover - plist parsing errors rare
                LOG.debug("Cannot read bundle id from %s: %s", plist_path, exc)
        self._bundle_cache[cache_key] = bundle_id
        return bundle_id

    def _workspace_app_info(self) -> Optional[Dict[str, Any]]:
        script = """
tell application "System Events"
    set frontApp to first process whose frontmost is true
    set procPid to unix id of frontApp as string
    set procName to name of frontApp as string
    try
        set bundleId to bundle identifier of frontApp as string
    on error
        set bundleId to ""
    end try
    return procPid & "|" & procName & "|" & bundleId
end tell
"""
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=1,
            )
        except Exception as exc:  # pragma: no cover - depends on macOS tooling
            self._log_workspace_failure(exc)
            return None

        if result.returncode != 0:
            message = result.stderr.strip() or "osascript returned non-zero status"
            self._log_workspace_failure(message)
            return None

        payload = result.stdout.strip()
        parts = payload.split("|", 2)
        if len(parts) != 3:
            self._log_workspace_failure(f"unexpected response: {payload!r}")
            return None

        pid_str, name, bundle_id = parts
        try:
            pid = int(pid_str)
        except ValueError:
            self._log_workspace_failure(f"invalid pid from workspace: {pid_str!r}")
            return None

        return {"pid": pid, "name": name or None, "bundle_id": bundle_id or None}

    def _log_workspace_failure(self, reason: Any) -> None:
        if not self._workspace_warning_emitted:
            LOG.warning("macOS workspace fallback failed: %s", reason)
            self._workspace_warning_emitted = True

    @staticmethod
    def _locate_app_bundle(executable: Path) -> Optional[Path]:
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent
        return None
