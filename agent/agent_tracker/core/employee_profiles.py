"""Durable device identities. An epoch's device and outbox never change owners."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from contextlib import contextmanager


class EmployeeProfiles:
    def __init__(self, state):
        self.state = state
        with self.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS employee_profiles (
                epoch TEXT PRIMARY KEY,
                device TEXT NOT NULL,
                identity TEXT,
                company_hash TEXT,
                source_epoch TEXT,
                key_fingerprint TEXT,
                status TEXT NOT NULL
            )""")
            if self._get(db, "employee_epoch") is None:
                device = self._get(db, "device")
                epoch = device["device_id"]
                identity = self._get(db, "identity")
                db.execute("INSERT INTO employee_profiles VALUES(?,?,?,?,?,?,?)", (
                    epoch, json.dumps(device), json.dumps(identity) if identity else None,
                    self._get(db, "company_code_hash"), None, None, "active"))
                self._set(db, "employee_epoch", epoch)
                self._set(db, "legacy_employee_epoch", epoch)

    @contextmanager
    def transaction(self):
        with self.state.lock:
            db = self.state.db
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _get(db, key, default=None):
        row = db.execute("SELECT value FROM state WHERE name=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set(db, key, value):
        db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (
            key, json.dumps(value, allow_nan=False)))

    @staticmethod
    def _profile(db, epoch):
        if not isinstance(epoch, str) or not re.fullmatch(r"[a-f0-9]{32}", epoch):
            raise ValueError("Invalid employee epoch")
        row = db.execute("""SELECT device,identity,company_hash,source_epoch,
            key_fingerprint,status FROM employee_profiles WHERE epoch=?""", (epoch,)).fetchone()
        if row is None:
            raise ValueError("Unknown employee epoch")
        return dict(epoch=epoch, device=json.loads(row[0]),
                    identity=json.loads(row[1]) if row[1] else None,
                    company_hash=row[2], source_epoch=row[3],
                    key_fingerprint=row[4], status=row[5])

    def active(self, *state_keys):
        with self.transaction() as db:
            profile = self._profile(db, self._get(db, "employee_epoch"))
            # The public state keys remain compatible with existing UI readers.
            profile["identity"] = self._get(db, "identity")
            profile["company_hash"] = self._get(db, "company_code_hash")
            for key in state_keys:
                profile[key] = self._get(db, key)
            return profile

    def get(self, epoch):
        with self.state.lock:
            return self._profile(self.state.db, epoch)

    def snapshot(self, *names):
        with self.transaction() as db:
            return {name: self._get(db, name) for name in names}

    def delivery_profiles(self):
        with self.transaction() as db:
            epochs = db.execute("""SELECT epoch FROM employee_profiles
                WHERE status IN ('active','retired') ORDER BY rowid""").fetchall()
            profiles = [self._profile(db, row[0]) for row in epochs]
            for profile in profiles:
                if profile["status"] == "active":
                    profile["identity"] = self._get(db, "identity")
            return profiles

    def update_active(self, epoch, **values):
        with self.transaction() as db:
            if self._get(db, "employee_epoch") != epoch:
                return False
            for name, value in values.items():
                self._set(db, name, value)
            if "identity" in values:
                db.execute("UPDATE employee_profiles SET identity=? WHERE epoch=?", (
                    json.dumps(values["identity"]), epoch))
            if "company_code_hash" in values:
                db.execute("UPDATE employee_profiles SET company_hash=? WHERE epoch=?", (
                    values["company_code_hash"], epoch))
            return True

    def prepare(self, source_epoch, company_hash, employee_key):
        with self.transaction() as db:
            if self._get(db, "employee_epoch") != source_epoch:
                raise ValueError("Employee changed while preparing the switch")
            source = self._profile(db, source_epoch)
            if not self._get(db, "identity") or company_hash != self._get(db, "company_code_hash"):
                raise ValueError("Employee switch requires the enrolled company")
            rows = db.execute("SELECT epoch FROM employee_profiles WHERE source_epoch=? AND status='candidate'",
                              (source_epoch,)).fetchall()
            for row in rows:
                candidate = self._profile(db, row[0])
                fingerprint = self._fingerprint(candidate["device"], employee_key)
                if hmac.compare_digest(candidate["key_fingerprint"], fingerprint):
                    return candidate
            device = dict(device_id=uuid.uuid4().hex, device_secret=secrets.token_hex(32))
            epoch = device["device_id"]
            db.execute("INSERT INTO employee_profiles VALUES(?,?,?,?,?,?,?)", (
                epoch, json.dumps(device), None, company_hash, source["epoch"],
                self._fingerprint(device, employee_key), "candidate"))
            return self._profile(db, epoch)

    @staticmethod
    def _fingerprint(device, employee_key):
        return hmac.new(device["device_secret"].encode("ascii"),
                        employee_key.encode("ascii"), hashlib.sha256).hexdigest()

    def record_candidate(self, epoch, identity):
        with self.transaction() as db:
            candidate = self._profile(db, epoch)
            source = self._profile(db, candidate["source_epoch"])
            if candidate["status"] != "candidate":
                raise ValueError("Employee switch is no longer pending")
            if identity["company_id"] != source["identity"]["company_id"]:
                raise ValueError("Employee must belong to the same company")
            if candidate["identity"] is not None and candidate["identity"] != identity:
                raise ValueError("Server attempted to change a prepared identity")
            db.execute("UPDATE employee_profiles SET identity=? WHERE epoch=?", (json.dumps(identity), epoch))

    def activate(self, epoch, expected_epoch):
        with self.transaction() as db:
            current_epoch = self._get(db, "employee_epoch")
            candidate = self._profile(db, epoch)
            if current_epoch == epoch and candidate["source_epoch"] == expected_epoch:
                return candidate["identity"]
            if current_epoch != expected_epoch or candidate["source_epoch"] != expected_epoch:
                raise ValueError("Employee changed while preparing the switch")
            current_identity = self._get(db, "identity")
            identity = candidate["identity"]
            if candidate["status"] != "candidate" or not identity:
                raise ValueError("Employee switch has not been enrolled")
            if identity["company_id"] != current_identity["company_id"]:
                raise ValueError("Employee must belong to the same company")
            if identity["user_id"] == current_identity["user_id"]:
                raise ValueError("This employee is already active")
            db.execute("UPDATE employee_profiles SET status='retired',identity=? WHERE epoch=?",
                       (json.dumps(current_identity), expected_epoch))
            db.execute("UPDATE employee_profiles SET status='active' WHERE epoch=?", (epoch,))
            for key, value in dict(employee_epoch=epoch, device=candidate["device"],
                                   identity=identity, policy={}, inventory_queued_at=0,
                                   last_delivery_at=0, last_web_delivery={}, error="",
                                   collection_blocked=False).items():
                self._set(db, key, value)
            return identity
