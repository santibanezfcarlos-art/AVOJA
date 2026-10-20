"""TTLock daily code integration (staging module; not wired into the web server).

No network requests occur on import. No scheduled task is installed.
Keep all credentials in Render private environment variables.
"""
import json
import base64
import hashlib
import hmac
import secrets
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CHILE = ZoneInfo("America/Santiago")
API = "https://api.sciener.com"
PASSWORD_MD5 = re.compile(r"^[a-fA-F0-9]{32}$")


class TTLockError(RuntimeError):
    """A safe-to-log TTLock failure without secrets."""


def validity_window(run_at=None):
    """Today's 22:00 through tomorrow's 22:00, in Chile local civil time.

    The UTC duration can be 23/24/25 hours at DST changes.
    """
    current = (run_at or datetime.now(timezone.utc)).astimezone(CHILE)
    start = datetime(current.year, current.month, current.day, 22, tzinfo=CHILE)
    tomorrow = current.date() + timedelta(days=1)
    end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 22, tzinfo=CHILE)
    return start, end


def timestamp_ms(value):
    return int(value.timestamp() * 1000)


def email_content(code, start):
    if not re.fullmatch(r"[0-9]{4,12}", str(code)):
        raise TTLockError("Invalid passcode format")
    next_day = start.date() + timedelta(days=1)
    months = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
              "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
    subject = f"Código de acceso {next_day.day} de {months[next_day.month - 1]}"
    body = f"Estimados,\n\nJunto con saludar, envío código de mañana:\n#{code}#\n\nSaludos!!"
    return subject, body


def _post_form(path, fields, *, transport=None):
    """POST form-urlencoded; inject transport for isolated mock tests."""
    data = urlencode(fields).encode("utf-8")
    request = Request(API + path, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "ValhallaTTLock/1.0",
    })
    try:
        opener = transport or urlopen
        with opener(request, timeout=15) as response:
            parsed = json.load(response)
    except (OSError, ValueError, TimeoutError) as exc:
        raise TTLockError("TTLock network or response failure; check status manually") from exc
    if not isinstance(parsed, dict):
        raise TTLockError("Unexpected TTLock response")
    return parsed


class TTLockClient:
    def __init__(self, client_id, client_secret, username, password_md5, lock_id, transport=None):
        if not PASSWORD_MD5.fullmatch(password_md5):
            raise ValueError("TTLOCK_PASSWORD_MD5 must contain 32 hexadecimal characters")
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password_md5 = password_md5.lower()
        self.lock_id = int(lock_id)
        self.transport = transport

    @classmethod
    def from_environment(cls, transport=None):
        keys = ["TTLOCK_CLIENT_ID", "TTLOCK_CLIENT_SECRET", "TTLOCK_USERNAME",
                "TTLOCK_PASSWORD_MD5", "TTLOCK_LOCK_ID"]
        missing = [k for k in keys if not os.environ.get(k)]
        if missing:
            raise ValueError("Missing TTLock environment variables: " + ", ".join(missing))
        return cls(*(os.environ[k] for k in keys), transport=transport)

    def authenticate(self):
        response = _post_form("/oauth2/token", {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "username": self.username,
            "password": self.password_md5,
        }, transport=self.transport)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise TTLockError("TTLock authentication rejected")
        return token

    def details(self, access_token):
        response = _post_form("/v3/lock/detail", {
            "clientId": self.client_id, "accessToken": access_token,
            "lockId": self.lock_id, "date": timestamp_ms(datetime.now(timezone.utc)),
        }, transport=self.transport)
        if response.get("errcode", 0) != 0 or response.get("lockId") != self.lock_id:
            raise TTLockError("TTLock details verification failed")
        if response.get("keyboardPwdVersion") != 4:
            raise TTLockError("Unsupported TTLock passcode version")
        return response

    def generate(self, access_token, start, end, version):
        """Non-idempotent. Never blindly retry after an uncertain response."""
        response = _post_form("/v3/keyboardPwd/get", {
            "clientId": self.client_id, "accessToken": access_token,
            "lockId": self.lock_id,
            "keyboardPwdName": "Daily " + start.date().isoformat(),
            "keyboardPwdType": 3,
            "startDate": timestamp_ms(start), "endDate": timestamp_ms(end),
            "keyboardPwdVersion": version,
            "date": timestamp_ms(datetime.now(timezone.utc)),
        }, transport=self.transport)
        if response.get("errcode", 0) != 0:
            raise TTLockError("TTLock rejected passcode creation")
        code, code_id = response.get("keyboardPwd"), response.get("keyboardPwdId")
        if not re.fullmatch(r"[0-9]{4,12}", str(code)) or not isinstance(code_id, int):
            raise TTLockError("TTLock did not confirm passcode creation")
        return str(code), code_id


class AttemptLedger:
    """Persistent daily claim. Uncertain attempts require manual reconciliation."""
    def __init__(self, path):
        self.path = str(path)

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS ttlock_attempts (
                local_day TEXT PRIMARY KEY, status TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                passcode_id INTEGER, error_category TEXT,
                encrypted_code TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(ttlock_attempts)")}
            if "encrypted_code" not in columns:
                db.execute("ALTER TABLE ttlock_attempts ADD COLUMN encrypted_code TEXT")

    def claim(self, local_day):
        stamp = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            result = db.execute(
                "INSERT OR IGNORE INTO ttlock_attempts"
                "(local_day,status,created_at,updated_at) VALUES (?,?,?,?)",
                (str(local_day), "generating", stamp, stamp))
            return result.rowcount == 1

    def mark(self, local_day, status, *, code_id=None, error_category=None):
        if status not in ("created", "emailed", "uncertain", "email_failed"):
            raise ValueError("Invalid TTLock status")
        with sqlite3.connect(self.path) as db:
            db.execute("""UPDATE ttlock_attempts SET status=?, updated_at=?,
                passcode_id=COALESCE(?,passcode_id),error_category=?
                WHERE local_day=?""", (status, datetime.now(timezone.utc).isoformat(),
                                      code_id, error_category, str(local_day)))


    def save_created(self, local_day, code_id, encrypted_code):
        if not encrypted_code:
            raise ValueError("Missing encrypted code")
        with sqlite3.connect(self.path) as db:
            cur = db.execute("""UPDATE ttlock_attempts
              SET status='created', passcode_id=?, encrypted_code=?,
                  error_category=NULL, updated_at=?
              WHERE local_day=? AND status='generating'""",
              (code_id, encrypted_code, datetime.now(timezone.utc).isoformat(), str(local_day)))
            if cur.rowcount != 1:
                raise RuntimeError("Attempt not in generating state")

    def get_existing(self, local_day):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            record = db.execute("""SELECT status, encrypted_code, passcode_id
                FROM ttlock_attempts WHERE local_day=?""", (str(local_day),)).fetchone()
            return dict(record) if record else None


def _encryption_key():
    """AES-GCM key (32 random bytes, urlsafe-base64 encoded) from private env."""
    raw = os.environ.get("TTLOCK_ENCRYPTION_KEY", "")
    try:
        key = base64.urlsafe_b64decode(raw.encode("ascii"))
    except (ValueError, UnicodeError):
        key = b""
    if len(key) != 32:
        raise ValueError("TTLOCK_ENCRYPTION_KEY must be urlsafe-base64 of 32 random bytes")
    return key


def encrypt_code(code):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    payload = AESGCM(_encryption_key()).encrypt(nonce, str(code).encode("ascii"), b"ttlock-code-v1")
    return base64.urlsafe_b64encode(nonce + payload).decode("ascii")


def decrypt_code(encrypted_code):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.urlsafe_b64decode(encrypted_code.encode("ascii"))
    if len(raw) < 29:
        raise ValueError("Invalid encrypted code")
    return AESGCM(_encryption_key()).decrypt(raw[:12], raw[12:], b"ttlock-code-v1").decode("ascii")


def prepare_no_network(run_at=None):
    """Safe smoke check: does not contact TTLock, Resend or change data."""
    start, end = validity_window(run_at)
    return {"local_day": start.date().isoformat(),
            "start_local": start.isoformat(), "end_local": end.isoformat(),
            "duration_hours": (end.timestamp() - start.timestamp()) / 3600}


if __name__ == "__main__":
    print(json.dumps(prepare_no_network(), ensure_ascii=False))
