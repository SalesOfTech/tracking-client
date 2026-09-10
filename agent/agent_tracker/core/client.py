from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from .dispatcher import validate_ack
from .event_queue import EventQueue
from .networking import HttpClient
from .files import read_json
from .version import application_version


def workspace() -> Path:
    if os.environ.get('SOFT_TRACKING_INSTALL'):
        return Path(os.environ['SOFT_TRACKING_INSTALL']).resolve().parent
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SOFT" / "TrackingV3"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "SOFT" / "TrackingV3"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "soft-tracking-v3"


def company_code_from_filename(filename: str) -> str:
    match = re.search(r"(?:^|[_-])([a-f0-9]{32})(?=\s*(?:\(\d+\))?\.[^.]+$)", Path(filename).name)
    return match.group(1) if match else ""


class ClientState:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.root / "state.sqlite3"), timeout=15, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state VALUES('device',?)", (json.dumps({"device_id": uuid.uuid4().hex, "device_secret": secrets.token_hex(32)}),))
        if os.name != "nt":
            (self.root / "state.sqlite3").chmod(0o600)

    def get(self, name: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, name: str, value) -> None:
        with self.lock, self.db:
            encoded = json.dumps(value, allow_nan=False)
            current = self.db.execute("SELECT value FROM state WHERE name=?", (name,)).fetchone()
            if not current or current[0] != encoded:
                self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (name, encoded))

    def close(self):
        self.db.close()


class Client:
    BASE_URL = "https://tracking.salesoftech.com"

    def __init__(self, root: Path = None, http=None):
        self.state = ClientState(root or workspace())
        self.outbox = EventQueue(self.state.root / "outbox.sqlite3")
        self.device = self.state.get("device")
        self.http = http or HttpClient(self.BASE_URL)
        self.http.session.headers.update({"Authorization": "Bearer " + self.device["device_secret"]})

    def enroll(self, company_code: str, employee_key: str) -> dict:
        install = os.environ.get("SOFT_TRACKING_INSTALL")
        profile = read_json(Path(install) / "enrollment.json", {}) if install else {}
        if profile.get("company_code") and profile["company_code"] != company_code:
            raise ValueError("The employee key must belong to the installer's company")
        if not re.fullmatch(r"[a-f0-9]{32}", company_code) or not re.fullmatch(r"[a-f0-9]{64}", employee_key):
            raise ValueError("Use the company installation code and a new v3 employee key")
        company_hash = hashlib.sha256(company_code.encode("ascii")).hexdigest()
        pinned = self.state.get("company_code_hash")
        if pinned and pinned != company_hash:
            raise ValueError("This installation is already assigned to another company")
        identity = self.state.get("identity")
        if identity:
            return identity
        os_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system())
        payload = dict(self.device, company_code=company_code, employee_key=employee_key, os=os_name, name=platform.node())
        result = self.http.post_json("/client/v3/enroll", payload)
        if result.get("ok") is not True or result.get("device_id") != self.device["device_id"] or not isinstance(result.get("company_id"), int) or result["company_id"] <= 0 or not isinstance(result.get("user_id"), int) or result["user_id"] <= 0:
            raise ValueError("Invalid enrollment response")
        safe = {key: result[key] for key in ("device_id", "company_id", "user_id", "company_name", "user_name")}
        self.state.set("company_code_hash", company_hash)
        self.state.set("identity", safe)
        return safe

    def refresh_config(self) -> dict:
        if not self.state.get("identity"):
            raise ValueError("Device is not registered")
        try:
            result = self.http.post_json("/client/v3/config", {"version": application_version()})
        except Exception as error:
            response = getattr(error, "response", None)
            if response is not None and response.status_code in (401, 403):
                self.state.set("policy", {})
            raise
        config = result.get("config")
        if result.get("ok") is not True or result.get("device_id") != self.device["device_id"] or not isinstance(config, dict):
            raise ValueError("Invalid configuration response")
        expires = config.get("policy_expires_at")
        if not isinstance(expires, int) or not time.time() < expires <= time.time() + 3700:
            raise ValueError("Invalid policy lifetime")
        self.state.set("policy", config)
        identity = result.get("identity")
        if identity is not None:
            previous = self.state.get("identity")
            if not isinstance(identity,dict) or any(identity.get(key)!=previous.get(key) for key in ("device_id","company_id","user_id")):
                self.state.set("policy",{})
                raise ValueError("Server attempted to change the device's company or employee")
            self.state.set("identity",{key:identity[key] for key in ("device_id","company_id","user_id","company_name","user_name")})
        return config

    def policy(self) -> dict:
        config = self.state.get("policy", {})
        if self.state.get("paused", False) or self.state.get("collection_blocked", False) or config.get("policy_expires_at", 0) <= time.time():
            return {"tracking": False, "interactions": False, "field_values": False, "ai": False, "domains": [], "track_processes": []}
        return config

    def flush(self) -> int:
        events = self.outbox.batch()
        if not events or not self.state.get("identity"):
            return 0
        result = self.http.post_json("/client/v3/events", {"events": events})
        ids, rejected = validate_ack(result, self.device["device_id"], events)
        self.outbox.acknowledge(ids)
        for event_id, code in rejected.items():
            self.outbox.mark_rejected(event_id, code)
        if ids:
            self.state.set('last_delivery_at', int(time.time()))
            confirmed = set(ids)
            sessions = [event for event in events if event['event_id'] in confirmed and event.get('type') == 'web_session']
            if sessions:
                latest = max(sessions, key=lambda event: event.get('end_timestamp', 0))
                previous = self.state.get('last_web_delivery', {})
                if latest.get('end_timestamp', 0) >= previous.get('end_timestamp', 0):
                    self.state.set('last_web_delivery', {
                        'confirmed_at': int(time.time()),
                        'hostname': urlparse(latest.get('url', '')).hostname or '',
                        'timestamp': latest.get('timestamp', 0),
                        'end_timestamp': latest.get('end_timestamp', 0),
                    })
        return len(ids)

    def queue_inventory(self):
        if not self.state.get('identity') or not self.policy().get('app_inventory'):
            return
        now = int(time.time())
        if now - self.state.get('inventory_queued_at', 0) < 86400:
            return
        from ..inventory import installed_apps
        rows = installed_apps()
        for start in range(0, len(rows), 20):
            self.outbox.push_payload(dict(event_id=uuid.uuid4().hex, type='app_inventory', timestamp=now, applications=rows[start:start+20]))
        self.state.set('inventory_queued_at', now)

    def status(self) -> dict:
        from ..i18n import client_language
        install = os.environ.get("SOFT_TRACKING_INSTALL")
        extension = read_json(Path(install) / "extension" / "manifest.json", {}) if install else {}
        enrollment = read_json(Path(install) / "enrollment.json", {}) if install else {}
        language = client_language(self.state.get("language", ""), enrollment)
        policy = self.policy()
        reason = 'recording'
        if not self.state.get('identity'):
            reason = 'unregistered'
        elif self.state.get('paused', False):
            reason = 'paused_local'
        elif self.state.get('collection_blocked', False):
            reason = 'collection_error'
        elif self.state.get('policy', {}).get('policy_expires_at', 0) <= time.time():
            reason = 'policy_expired'
        elif not (policy.get('tracking') or policy.get('interactions')):
            reason = 'inventory_only' if policy.get('app_inventory') else 'disabled_policy'
        return {"identity": self.state.get("identity"), "queue": self.outbox.counts(), "policy": self.policy(), "paused": self.state.get("paused", False), "error": self.state.get("error", ""),
                "collection_reason": reason,
                "language": language, "version": application_version(),
                "extension_version": extension.get("version"), "retry_generation":self.state.get("retry_generation",0)}

    def close(self):
        self.outbox.close()
        self.state.close()
