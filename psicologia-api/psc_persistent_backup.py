"""Authenticated, write-only receiver for encrypted PSC Postgres snapshots.

Accepts ciphertext, never plaintext. Requires the existing Render persistent disk.
No route for downloading backups or viewing patient data.
"""
import hashlib
import hmac
import os
import re
import tempfile
import time
from pathlib import Path
from flask import Blueprint, jsonify, request

bp = Blueprint("psc_persistent_backup", __name__)
BASE = Path("/var/data/psc-postgres-backups")
MAX_BYTES = 24 * 1024 * 1024


@bp.post("/internal/psc/postgres-backup/upload")
def upload():
    key = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    ts = request.headers.get("X-PSC-Backup-Timestamp", "")
    sig = request.headers.get("X-PSC-Backup-Signature", "")
    name = request.headers.get("X-PSC-Backup-Name", "")
    if (len(key) < 40 or not re.fullmatch(r"[0-9]{10}", ts)
            or abs(int(ts) - int(time.time())) > 120
            or not re.fullmatch(r"[a-f0-9]{64}", sig)
            or not re.fullmatch(r"psc-pg-[0-9]{8}T[0-9]{6}Z\.enc", name)):
        return jsonify(state="NOT_AUTHORIZED"), 403
    if request.content_length is None or request.content_length > MAX_BYTES or request.content_length < 100:
        return jsonify(state="INVALID_SIZE"), 413
    payload = request.get_data(cache=False)
    if not 100 <= len(payload) <= MAX_BYTES:
        return jsonify(state="INVALID_SIZE"), 413
    expected = hmac.new(key.encode(), ts.encode() + b"." + name.encode() + b"." + payload,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return jsonify(state="NOT_AUTHORIZED"), 403
    if not os.path.ismount("/var/data"):
        return jsonify(state="NO_PERSISTENT_DISK"), 503
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    dst = BASE / name
    if dst.exists():
        return jsonify(state="ALREADY_PRESENT", sha256=hashlib.sha256(dst.read_bytes()).hexdigest()), 200
    fd, tmpname = tempfile.mkstemp(prefix=".psc-", dir=BASE)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(tmpname, 0o600)
        os.link(tmpname, dst)  # Never overwrite a snapshot of the same name.
        os.unlink(tmpname)
        return jsonify(state="STORED", sha256=hashlib.sha256(payload).hexdigest()), 201
    finally:
        if os.path.exists(tmpname):
            os.unlink(tmpname)


def verify_latest_encrypted_snapshot():
    """Decrypt latest backup in memory, check SQLite integrity and row parity.

    No patient data is returned or logged; plaintext is never written to disk.
    """
    import base64
    import sqlite3
    from cryptography.fernet import Fernet
    secret = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    if len(secret) < 40:
        raise RuntimeError("Backup decryption key unavailable")
    files = sorted(BASE.glob("psc-pg-*.enc"), reverse=True)
    if not files:
        raise FileNotFoundError("No encrypted snapshots on disk")
    encrypted = files[0].read_bytes()
    if not 100 <= len(encrypted) <= MAX_BYTES:
        raise RuntimeError("Encrypted snapshot size invalid")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"psc-postgres-offsite-v1:" + secret.encode()).digest()
    )
    raw = Fernet(key).decrypt(encrypted)
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(raw)
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check failed")
        source = dict(db.execute(
            "SELECT source_table,source_count FROM snapshot_metadata"
        ).fetchall())
        seen = dict(db.execute(
            "SELECT source_table,COUNT(*) FROM snapshot_rows GROUP BY source_table"
        ).fetchall())
        if len(source) < 8 or any(seen.get(table, 0) != n for table, n in source.items()):
            raise RuntimeError("Snapshot row parity failed")
        return {"state": "DECRYPTED_SQLITE_INTEGRITY_AND_PARITY_OK",
                "tables": len(source), "rows": sum(source.values()),
                "ciphertext_sha256": hashlib.sha256(encrypted).hexdigest()}
    finally:
        db.close()
