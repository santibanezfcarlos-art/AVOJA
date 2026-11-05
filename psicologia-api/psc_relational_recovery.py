"""Materialize an encrypted PostgreSQL snapshot as an independent SQLite recovery DB.

NEVER replaces billing data or changes the live source. Designed for offline
restoration and eventual controlled cutover, not an automatic runtime switch.
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from psc_persistent_backup import BASE, MAX_BYTES

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE psychology_booking_days (
 slot_date TEXT PRIMARY KEY
);
CREATE TABLE psychology_booking_holds (
 id TEXT PRIMARY KEY,
 cancel_secret_sha256 TEXT NOT NULL,
 slot_date TEXT NOT NULL REFERENCES psychology_booking_days(slot_date),
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 service TEXT NOT NULL, status TEXT NOT NULL,
 expires_at TEXT NOT NULL, created_at TEXT, updated_at TEXT
);
CREATE TABLE psychology_demo_payments (
 order_id TEXT PRIMARY KEY,
 hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
 session_id TEXT NOT NULL UNIQUE, token_sha256 TEXT UNIQUE,
 service TEXT NOT NULL, amount_clp INTEGER NOT NULL,
 test_recipient TEXT NOT NULL, state TEXT NOT NULL,
 calendar_event_id TEXT, created_at TEXT, updated_at TEXT, verified_at TEXT,
 patient_name TEXT
);
CREATE TABLE psychology_demo_notifications (
 order_id TEXT NOT NULL REFERENCES psychology_demo_payments(order_id),
 recipient_role TEXT NOT NULL, status TEXT NOT NULL,
 attempted_at TEXT, sent_at TEXT, timezone_correction_sent_at TEXT,
 PRIMARY KEY(order_id,recipient_role)
);
CREATE TABLE psychology_reschedule_links (
 order_id TEXT PRIMARY KEY REFERENCES psychology_demo_payments(order_id),
 token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT, redeemed_at TEXT, revoked_at TEXT
);
CREATE TABLE psychology_reschedule_audit (
 id INTEGER PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES psychology_demo_payments(order_id),
 old_starts_at TEXT NOT NULL, new_starts_at TEXT NOT NULL,
 changed_at TEXT, channel TEXT, UNIQUE(order_id)
);
CREATE TABLE psychology_google_oauth (
 key TEXT PRIMARY KEY, encrypted_refresh_token TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE psychology_fonasa_bookings (
 source_id TEXT PRIMARY KEY,
 hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
 patient_name TEXT NOT NULL, patient_email TEXT NOT NULL,
 token_sha256 TEXT NOT NULL UNIQUE, created_at TEXT, redeemed_at TEXT,
 old_starts_at TEXT, new_starts_at TEXT
);
CREATE INDEX psychology_holds_date_state ON psychology_booking_holds(slot_date,status,expires_at);
CREATE INDEX psychology_demo_payments_states ON psychology_demo_payments(state,created_at);
"""
EXPECTED = [
 "psychology_booking_days", "psychology_booking_holds",
 "psychology_demo_payments", "psychology_demo_notifications",
 "psychology_reschedule_links", "psychology_reschedule_audit",
 "psychology_google_oauth", "psychology_fonasa_bookings"
]


def _decrypt(latest):
    secret = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    if len(secret) < 40:
        raise RuntimeError("Strong snapshot decryption key missing")
    blob = latest.read_bytes()
    if len(blob) < 100 or len(blob) > MAX_BYTES:
        raise RuntimeError("Encrypted archive size invalid")
    key = base64.urlsafe_b64encode(hashlib.sha256(
        b"psc-postgres-offsite-v1:" + secret.encode()).digest())
    return Fernet(key).decrypt(blob)


def _value(v):
    if isinstance(v, dict) and set(v) == {"__binary_base64__"}:
        return base64.b64decode(v["__binary_base64__"], validate=True)
    if isinstance(v, (str, int, float, bytes)) or v is None:
        return v
    if isinstance(v, bool):
        return int(v)
    raise ValueError("Unrecognized serialized column")


def materialize():
    """Produces a new file with 0600 permissions; never modifies live billing DB."""
    files = sorted(BASE.glob("psc-pg-*.enc"), reverse=True)
    if not files:
        raise FileNotFoundError("Missing encrypted PostgreSQL snapshot")
    raw = _decrypt(files[0])
    input_db = sqlite3.connect(":memory:")
    try:
        input_db.deserialize(raw)
        if input_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Snapshot integrity failure")
        counts = dict(input_db.execute(
            "SELECT source_table, source_count FROM snapshot_metadata").fetchall())
        if set(counts) != set(EXPECTED):
            raise RuntimeError("Snapshot tables differ from expected application schema")
        recovery_dir = Path("/var/data/psc-postgres-recovery")
        if not os.path.ismount("/var/data"):
            raise RuntimeError("Persistent disk unavailable")
        recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        final = recovery_dir / (files[0].stem + ".sqlite3")
        if final.exists():
            raise FileExistsError("Recovery DB already exists; will not overwrite")
        fd, tempname = tempfile.mkstemp(prefix=".psc-recovery-", dir=recovery_dir)
        os.close(fd)
        try:
            result = sqlite3.connect(tempname)
            try:
                result.executescript(SCHEMA)
                for table in EXPECTED:
                    quoted_table = '"' + table + '"'
                    actual_cols = [r[1] for r in result.execute(
                        "PRAGMA table_info(" + quoted_table + ")").fetchall()]
                    source_rows = input_db.execute(
                        "SELECT payload_json FROM snapshot_rows WHERE source_table=? ORDER BY row_index",
                        (table,)).fetchall()
                    if len(source_rows) != counts[table]:
                        raise RuntimeError("Source count mismatch: " + table)
                    # A field added in PG must never be silently dropped.
                    for (payload,) in source_rows:
                        fields = json.loads(payload)
                        if not isinstance(fields, dict):
                            raise ValueError("Invalid JSON object")
                        for field in fields:
                            if (not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*",field)
                                    or field.startswith("sqlite_")):
                                raise ValueError("Invalid source column")
                            if field not in actual_cols:
                                result.execute("ALTER TABLE " + quoted_table
                                    + ' ADD COLUMN "' + field + '" TEXT')
                                actual_cols.append(field)
                        columns = list(fields)
                        col_sql = ",".join('"' + x + '"' for x in columns)
                        vals = [_value(fields[x]) for x in columns]
                        result.execute("INSERT INTO " + quoted_table
                            + " (" + col_sql + ") VALUES ("
                            + ",".join("?" for _ in columns) + ")", vals)
                violations = result.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError("Recovery foreign key violations")
                if result.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Recovery integrity check failed")
                for table in EXPECTED:
                    if result.execute("SELECT COUNT(*) FROM " + '"' + table + '"').fetchone()[0] != counts[table]:
                        raise RuntimeError("Recovery row parity failure")
                result.commit()
            finally:
                result.close()
            os.chmod(tempname, 0o600)
            os.link(tempname, final)
        finally:
            if os.path.exists(tempname):
                os.unlink(tempname)
        return {"state": "RELATIONAL_SQLITE_RESTORED_AND_VERIFIED",
                "tables": len(counts), "rows": sum(counts.values()),
                "source_archive": files[0].name}
    finally:
        input_db.close()
