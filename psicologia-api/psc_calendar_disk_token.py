"""Signed read-only access to OAuth ciphertext saved on PSC's persistent disk.

The Google refresh token remains Fernet-encrypted under the separate OAuth
key held only by the existing Calendar service. Never returns plaintext.
"""
import hashlib
import hmac
import re
import sqlite3
import time
from pathlib import Path
from flask import Blueprint, jsonify, request

bp = Blueprint("psc_calendar_disk_token", __name__)
PATH = "/internal/psc/calendar/oauth-ciphertext"
RECOVERY = Path("/var/data/psc-postgres-recovery")


@bp.get(PATH)
def ciphertext():
    key = __import__("os").environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    stamp = request.headers.get("X-PSC-Timestamp", "")
    signature = request.headers.get("X-PSC-Signature", "")
    if (len(key) < 40 or not re.fullmatch(r"[0-9]{10}", stamp)
            or abs(int(stamp) - int(time.time())) > 120
            or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        return jsonify(state="NOT_AUTHORIZED"), 403
    expected = hmac.new(key.encode(), (stamp + ".GET " + PATH).encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify(state="NOT_AUTHORIZED"), 403
    if not Path("/var/data").is_mount():
        return jsonify(state="PERSISTENT_DISK_UNAVAILABLE"), 503
    # This is a read-only snapshot source, not a writable OAuth ledger.
    candidates = sorted(RECOVERY.glob("psc-pg-*.sqlite3"), reverse=True)
    if not candidates:
        return jsonify(state="SNAPSHOT_UNAVAILABLE"), 503
    db = sqlite3.connect("file:" + str(candidates[0]) + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        record = db.execute(
            "SELECT encrypted_refresh_token FROM psychology_google_oauth WHERE key=?",
            ("owner",)).fetchone()
    except sqlite3.Error:
        return jsonify(state="SNAPSHOT_READ_FAILED"), 503
    finally:
        db.close()
    if not record or not isinstance(record[0], str) or not 100 <= len(record[0]) <= 8192:
        return jsonify(state="NOT_CONFIGURED"), 503
    response = jsonify(state="OK", encrypted_refresh_token=record[0])
    response.headers["Cache-Control"] = "no-store"
    return response
