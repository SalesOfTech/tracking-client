from __future__ import annotations

import json
import hashlib
import os
import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Sequence


@dataclass
class Event:
    type: str
    timestamp: int
    exe: str
    application_id: str | None = None
    end_timestamp: int | None = None
    duration_sec: int | None = None
    reason: str | None = None
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        payload = {k: v for k, v in asdict(self).items() if v is not None}
        return payload


class EventQueue:
    """Durable outbox. Reading a batch never removes its events."""

    def __init__(self, path: Path, reserve_bytes: int = 16 * 1024 * 1024) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.reserve_bytes = reserve_bytes
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), timeout=15, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS receipts (
                event_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL
            );
        """)
        if os.name != "nt":
            self.path.chmod(0o600)

    def push(self, event: Event) -> None:
        self.push_payload(event.to_dict())

    def push_payload(self, event: dict) -> bool:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or len(event_id) != 32 or any(c not in "0123456789abcdef" for c in event_id):
            raise ValueError("Invalid event ID")
        payload = json.dumps(event, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > 64 * 1024:
            raise ValueError("Event exceeds 64 KiB")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._lock, self._db:
            for table in ("receipts", "outbox"):
                existing = self._db.execute("SELECT payload_hash FROM " + table + " WHERE event_id=?", (event_id,)).fetchone()
                if existing:
                    if existing[0] != digest:
                        raise ValueError("Event ID reused with a different payload")
                    return table == "receipts"
            if shutil.disk_usage(self.path.parent).free < self.reserve_bytes:
                raise OSError("Outbox disk reserve reached; collection paused, pending events retained")
            self._db.execute("INSERT INTO outbox(event_id,payload,payload_hash) VALUES(?,?,?)", (event_id, payload, digest))
        return False

    def batch(self, limit: int = 100) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT payload FROM outbox WHERE error IS NULL ORDER BY sequence LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, event_ids: Sequence[str]) -> None:
        with self._lock, self._db:
            for event_id in event_ids:
                self._db.execute("INSERT OR IGNORE INTO receipts SELECT event_id,payload_hash FROM outbox WHERE event_id=?", (event_id,))
                self._db.execute("DELETE FROM outbox WHERE event_id=?", (event_id,))

    def mark_rejected(self, event_id: str, code: str) -> None:
        # Quarantine preserves the payload while letting valid later events through.
        with self._lock, self._db:
            self._db.execute("UPDATE outbox SET error=? WHERE event_id=?", (code[:80], event_id))

    def retry_rejected(self) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE outbox SET error=NULL")

    def confirmed(self, event_ids: Sequence[str]) -> List[str]:
        with self._lock:
            return [event_id for event_id in event_ids if self._db.execute("SELECT 1 FROM receipts WHERE event_id=?", (event_id,)).fetchone()]

    def counts(self) -> dict:
        with self._lock:
            total, rejected = self._db.execute("SELECT COUNT(*), COALESCE(SUM(error IS NOT NULL),0) FROM outbox").fetchone()
        return {"pending": total - rejected, "rejected": rejected}

    def rejections(self, event_ids: Sequence[str]) -> dict:
        result = {}
        with self._lock:
            for event_id in event_ids:
                row = self._db.execute("SELECT error FROM outbox WHERE event_id=? AND error IS NOT NULL", (event_id,)).fetchone()
                if row:
                    result[event_id] = row[0]
        return result

    def drain(self) -> List[Event]:
        raise RuntimeError("Destructive drain is forbidden; use batch and database acknowledgement")

    def requeue_front(self, events: Sequence[Event]) -> None:
        for event in events:
            self.push(event)

    def snapshot_json(self) -> str:
        return json.dumps(self.batch())

    def close(self) -> None:
        with self._lock:
            self._db.close()


def current_timestamp() -> int:
    return int(time.time())
