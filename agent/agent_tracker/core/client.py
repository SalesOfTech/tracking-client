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
from .employee_profiles import EmployeeProfiles
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
        self.profiles = EmployeeProfiles(self.state)
        self._lock = threading.RLock()
        self._outboxes = {}
        self.http = http or HttpClient(self.BASE_URL)
        self.http.session.headers.update({"Authorization": "Bearer " + self.device["device_secret"]})
        self._queue(self.profiles.active())

    @property
    def device(self):
        return self.profiles.active()["device"]

    @property
    def employee_epoch(self):
        return self.state.get("employee_epoch")

    @property
    def outbox(self):
        """The returned queue stays bound to its employee, even after switching."""
        return self._queue(self.profiles.active())

    def _queue(self, profile):
        epoch = profile["epoch"]
        with self._lock:
            if epoch not in self._outboxes:
                legacy = self.state.get("legacy_employee_epoch")
                path = self.state.root / "outbox.sqlite3" if epoch == legacy else self.state.root / "employee-outboxes" / (epoch + ".sqlite3")
                self._outboxes[epoch] = EventQueue(path)
            return self._outboxes[epoch]

    def outbox_for_epoch(self, epoch):
        """Native-host routing: never infer the owner of an untagged old event."""
        if epoch is None:
            epoch = self.employee_epoch
            if epoch != self.state.get("legacy_employee_epoch"):
                raise ValueError("Employee epoch is required after switching")
        profile = self.profiles.get(epoch)
        if profile["status"] not in ("active", "retired") or not profile["identity"]:
            raise ValueError("Employee epoch is not enrolled")
        return self._queue(profile)

    def _post(self, profile, path, payload):
        # A single Session is shared by UI and worker calls, never by credentials.
        with self._lock:
            headers = self.http.session.headers
            previous = headers.get("Authorization")
            headers["Authorization"] = "Bearer " + profile["device"]["device_secret"]
            try:
                return self.http.post_json(path, payload)
            finally:
                if previous is None:
                    headers.pop("Authorization", None)
                else:
                    headers["Authorization"] = previous

    def _company_hash(self, company_code, employee_key):
        install = os.environ.get("SOFT_TRACKING_INSTALL")
        profile = read_json(Path(install) / "enrollment.json", {}) if install else {}
        if profile.get("company_code") and profile["company_code"] != company_code:
            raise ValueError("The employee key must belong to the installer's company")
        if not isinstance(company_code, str) or not isinstance(employee_key, str) or not re.fullmatch(r"[a-f0-9]{32}", company_code) or not re.fullmatch(r"[a-f0-9]{64}", employee_key):
            raise ValueError("Use the company installation code and a new v3 employee key")
        company_hash = hashlib.sha256(company_code.encode("ascii")).hexdigest()
        pinned = self.state.get("company_code_hash")
        if pinned and pinned != company_hash:
            raise ValueError("This installation is already assigned to another company")
        return company_hash

    @staticmethod
    def _enrollment_identity(result, device):
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("device_id") != device["device_id"] or any(type(result.get(key)) is not int or result[key] <= 0 for key in ("company_id", "user_id")) or any(not isinstance(result.get(key), str) for key in ("company_name", "user_name")):
            raise ValueError("Invalid enrollment response")
        return {key: result[key] for key in ("device_id", "company_id", "user_id", "company_name", "user_name")}

    def _enroll_profile(self, profile, company_code, employee_key):
        os_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system())
        payload = dict(profile["device"], company_code=company_code, employee_key=employee_key, os=os_name, name=platform.node())
        return self._enrollment_identity(self._post(profile, "/client/v3/enroll", payload), profile["device"])

    def enroll(self, company_code: str, employee_key: str) -> dict:
        company_hash = self._company_hash(company_code, employee_key)
        profile = self.profiles.active()
        if profile["identity"]:
            return profile["identity"]
        identity = self._enroll_profile(profile, company_code, employee_key)
        if not self.profiles.update_active(profile["epoch"], company_code_hash=company_hash, identity=identity):
            raise ValueError("Employee changed during enrollment")
        return identity

    def prepare_employee_switch(self, company_code: str, employee_key: str) -> dict:
        """Enroll a persisted candidate without changing collection or delivery."""
        company_hash = self._company_hash(company_code, employee_key)
        current = self.profiles.active()
        candidate = self.profiles.prepare(current["epoch"], company_hash, employee_key)
        identity = candidate["identity"]
        if identity is None:
            identity = self._enroll_profile(candidate, company_code, employee_key)
            self.profiles.record_candidate(candidate["epoch"], identity)
        if identity["user_id"] == current["identity"]["user_id"]:
            raise ValueError("This employee is already active")
        return {"epoch": candidate["epoch"], "expected_epoch": current["epoch"], "identity": identity}

    def activate_employee_switch(self, epoch, *, expected_epoch, desktop_sessions_closed=False,
                                 browser_epoch_ready=False):
        """Caller holds the worker barrier and verifies durable session closure.

        Browser readiness attests that all browser writers preserve source epochs,
        including durable pending rows and sessions. A legacy writer blocks switch.
        """
        if desktop_sessions_closed is not True or browser_epoch_ready is not True:
            raise ValueError("Employee switch requires desktop and browser epoch barriers")
        with self._lock:
            candidate = self.profiles.get(epoch)
            self._queue(candidate)
            identity = self.profiles.activate(epoch, expected_epoch)
            self.http.session.headers["Authorization"] = "Bearer " + candidate["device"]["device_secret"]
            return identity

    def refresh_config(self) -> dict:
        profile = self.profiles.active()
        if not profile["identity"]:
            raise ValueError("Device is not registered")
        try:
            result = self._post(profile, "/client/v3/config", {"version": application_version()})
        except Exception as error:
            response = getattr(error, "response", None)
            if response is not None and response.status_code in (401, 403):
                self.profiles.update_active(profile["epoch"], policy={})
            raise
        config = result.get("config") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("ok") is not True or result.get("device_id") != profile["device"]["device_id"] or not isinstance(config, dict):
            raise ValueError("Invalid configuration response")
        expires = config.get("policy_expires_at")
        if not isinstance(expires, int) or not time.time() < expires <= time.time() + 3700:
            raise ValueError("Invalid policy lifetime")
        identity = result.get("identity")
        values = {"policy": config}
        if identity is not None:
            previous = profile["identity"]
            if not isinstance(identity,dict) or any(identity.get(key)!=previous.get(key) for key in ("device_id","company_id","user_id")) or any(not isinstance(identity.get(key), str) for key in ("company_name", "user_name")):
                self.profiles.update_active(profile["epoch"], policy={})
                raise ValueError("Server attempted to change the device's company or employee")
            values["identity"] = {key:identity[key] for key in ("device_id","company_id","user_id","company_name","user_name")}
        if not self.profiles.update_active(profile["epoch"], **values):
            raise ValueError("Employee changed during configuration refresh")
        return config

    def policy(self) -> dict:
        return self._policy(self.profiles.snapshot("policy", "paused", "collection_blocked"))

    @staticmethod
    def _policy(snapshot):
        config = snapshot.get("policy") or {}
        if snapshot.get("paused") or snapshot.get("collection_blocked") or config.get("policy_expires_at", 0) <= time.time():
            return {"tracking": False, "interactions": False, "field_values": False, "ai": False, "domains": [], "track_processes": []}
        return config

    def flush(self) -> int:
        delivered = 0
        failure = None
        for profile in self.profiles.delivery_profiles():
            try:
                with self._lock:
                    delivered += self._flush_profile(profile)
            except Exception as error:
                # One revoked or offline employee must not starve other profiles.
                if failure is None:
                    failure = error
        if failure is not None:
            raise failure
        return delivered

    def _flush_profile(self, profile):
        outbox = self._queue(profile)
        events = outbox.batch()
        if not events or not profile["identity"]:
            return 0
        result = self._post(profile, "/client/v3/events", {"events": events})
        ids, rejected = validate_ack(result, profile["device"]["device_id"], events)
        outbox.acknowledge(ids)
        for event_id, code in rejected.items():
            outbox.mark_rejected(event_id, code)
        if ids:
            values = {'last_delivery_at': int(time.time())}
            confirmed = set(ids)
            sessions = [event for event in events if event['event_id'] in confirmed and event.get('type') == 'web_session']
            if sessions:
                latest = max(sessions, key=lambda event: event.get('end_timestamp', 0))
                previous = self.state.get('last_web_delivery', {})
                if latest.get('end_timestamp', 0) >= previous.get('end_timestamp', 0):
                    values['last_web_delivery'] = {
                        'confirmed_at': int(time.time()),
                        'hostname': urlparse(latest.get('url', '')).hostname or '',
                        'timestamp': latest.get('timestamp', 0),
                        'end_timestamp': latest.get('end_timestamp', 0),
                    }
            self.profiles.update_active(profile["epoch"], **values)
        return len(ids)

    def queue_counts(self):
        counts = {"pending": 0, "rejected": 0}
        for profile in self.profiles.delivery_profiles():
            for key, value in self._queue(profile).counts().items():
                counts[key] += value
        return counts

    def retry_rejected(self):
        for profile in self.profiles.delivery_profiles():
            self._queue(profile).retry_rejected()

    def queue_inventory(self):
        profile = self.profiles.active('policy', 'paused', 'collection_blocked', 'inventory_queued_at')
        if not profile['identity'] or not self._policy(profile).get('app_inventory'):
            return
        now = int(time.time())
        if now - (profile['inventory_queued_at'] or 0) < 86400:
            return
        from ..inventory import installed_apps
        rows = installed_apps()
        for start in range(0, len(rows), 20):
            self._queue(profile).push_payload(dict(event_id=uuid.uuid4().hex, type='app_inventory', timestamp=now, applications=rows[start:start+20]))
        self.profiles.update_active(profile['epoch'], inventory_queued_at=now)

    def status(self) -> dict:
        from ..i18n import client_language
        install = os.environ.get("SOFT_TRACKING_INSTALL")
        extension = read_json(Path(install) / "extension" / "manifest.json", {}) if install else {}
        enrollment = read_json(Path(install) / "enrollment.json", {}) if install else {}
        snapshot = self.profiles.snapshot("identity", "employee_epoch", "legacy_employee_epoch", "policy",
                                          "paused", "collection_blocked", "language", "error", "retry_generation")
        language = client_language(snapshot.get("language") or "", enrollment)
        policy = self._policy(snapshot)
        reason = 'recording'
        if not snapshot['identity']:
            reason = 'unregistered'
        elif snapshot['paused']:
            reason = 'paused_local'
        elif snapshot['collection_blocked']:
            reason = 'collection_error'
        elif (snapshot['policy'] or {}).get('policy_expires_at', 0) <= time.time():
            reason = 'policy_expired'
        elif not (policy.get('tracking') or policy.get('interactions')):
            reason = 'inventory_only' if policy.get('app_inventory') else 'disabled_policy'
        return {"identity": snapshot['identity'], "queue": self.queue_counts(), "policy": policy, "paused": bool(snapshot['paused']), "error": snapshot['error'] or "",
                "employee_epoch": snapshot['employee_epoch'], "legacy_employee_epoch": snapshot['legacy_employee_epoch'],
                "collection_reason": reason,
                "language": language, "version": application_version(),
                "extension_version": extension.get("version"), "retry_generation":snapshot['retry_generation'] or 0}

    def close(self):
        with self._lock:
            for outbox in self._outboxes.values():
                outbox.close()
            self._outboxes.clear()
            self.state.close()
