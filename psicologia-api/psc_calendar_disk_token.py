"""HMAC-protected OAuth ciphertext store on PSC's existing persistent disk.

Only already-Fernet-encrypted refresh tokens cross this signed link.
Neither secrets nor decrypted tokens are returned to public clients.
"""
import hashlib
import hmac
import os
import re
import sqlite3
import time
from pathlib import Path
from flask import Blueprint, jsonify, request

bp = Blueprint("psc_calendar_disk_token", __name__)
PATH = "/internal/psc/calendar/oauth-ciphertext"
RECOVERY = Path("/var/data/psc-postgres-recovery")
STORE = Path("/var/data/psc-google-oauth.sqlite3")


def _authorized(method, data=b""):
    key = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    stamp = request.headers.get("X-PSC-Timestamp", "")
    signature = request.headers.get("X-PSC-Signature", "")
    if (len(key) < 40 or not re.fullmatch(r"[0-9]{10}", stamp)
            or abs(int(stamp) - int(time.time())) > 120
            or not re.fullmatch(r"[0-9a-f]{64}", signature)):
        return False
    suffix = ("." + hashlib.sha256(data).hexdigest()) if method == "POST" else ""
    message = (stamp + "." + method + " " + PATH + suffix).encode()
    expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _read_one(path):
    db = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True, timeout=3)
    try:
        db.execute("PRAGMA query_only=ON")
        return db.execute(
            "SELECT encrypted_refresh_token FROM psychology_google_oauth WHERE key=?",
            ("owner",)).fetchone()
    finally:
        db.close()


@bp.get(PATH)
def ciphertext():
    if not _authorized("GET"):
        return jsonify(state="NOT_AUTHORIZED"), 403
    if not os.path.ismount("/var/data"):
        return jsonify(state="PERSISTENT_DISK_UNAVAILABLE"), 503
    candidates = ([STORE] if STORE.is_file() else []) + sorted(
        RECOVERY.glob("psc-pg-*.sqlite3"), reverse=True)
    try:
        record = None
        for source in candidates:
            record = _read_one(source)
            if record:
                break
    except sqlite3.Error:
        return jsonify(state="STORAGE_READ_FAILED"), 503
    if not record or not isinstance(record[0], str) or not 100 <= len(record[0]) <= 8192:
        return jsonify(state="NOT_CONFIGURED"), 503
    response = jsonify(state="OK", encrypted_refresh_token=record[0])
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post(PATH)
def save_ciphertext():
    if (request.content_length is None or request.content_length > 10000
            or request.content_length < 120):
        return jsonify(state="INVALID_SIZE"), 413
    body = request.get_data(cache=False)
    if not _authorized("POST", body):
        return jsonify(state="NOT_AUTHORIZED"), 403
    if not os.path.ismount("/var/data"):
        return jsonify(state="PERSISTENT_DISK_UNAVAILABLE"), 503
    try:
        import json
        doc = json.loads(body)
        token = doc["encrypted_refresh_token"]
        if (not isinstance(token, str) or not 100 <= len(token) <= 8192
                or not re.fullmatch(r"[A-Za-z0-9_-]+=*", token)):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        return jsonify(state="INVALID_CIPHERTEXT"), 422
    # Keep a separate operational credential independent of snapshot refresh.
    if not STORE.exists():
        try:
            fd = os.open(STORE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
    db = sqlite3.connect(STORE, timeout=10)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("""CREATE TABLE IF NOT EXISTS psychology_google_oauth (
            key TEXT PRIMARY KEY, encrypted_refresh_token TEXT NOT NULL,
            updated_at TEXT NOT NULL)""")
        db.execute("""INSERT INTO psychology_google_oauth
            (key, encrypted_refresh_token, updated_at)
            VALUES ('owner', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE
            SET encrypted_refresh_token=excluded.encrypted_refresh_token,
                updated_at=excluded.updated_at""", (token,))
        db.commit()
    except sqlite3.Error:
        return jsonify(state="STORAGE_WRITE_FAILED"), 503
    finally:
        db.close()
    os.chmod(STORE, 0o600)
    return jsonify(state="STORED"), 200
