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
            or not re.fullmatch(r"psc-pg-[0-9]{8}T[0-9]{6}Z\\.enc", name)):
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
