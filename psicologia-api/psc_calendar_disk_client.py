"""Signed PSC OAuth ciphertext bridge with local-only request-path caching.

Never synchronously calls the public PSC API during a Calendar request.
That avoids circular HTTP deadlocks when PSC calls the Calendar pilot.
"""
import hashlib
import hmac
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

PATH = "/internal/psc/calendar/oauth-ciphertext"
URL = "https://psicologia-carlos-calendar-api.onrender.com" + PATH
CACHE = Path("/tmp/psc-google-oauth-ciphertext.cache")


def _shared_secret():
    secret = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    if len(secret) < 40:
        raise RuntimeError("PSC signed disk bridge unavailable")
    return secret


def _write_cache(encrypted):
    if not isinstance(encrypted, str) or not 100 <= len(encrypted) <= 8192:
        raise ValueError("Invalid encrypted OAuth data")
    fd, temporary = tempfile.mkstemp(prefix=".psc-oauth-", dir=CACHE.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, CACHE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def refresh_cached_ciphertext():
    """Server-to-server operation: call only from bootstrap/background, never request."""
    secret = _shared_secret()
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), (stamp + ".GET " + PATH).encode(),
                         hashlib.sha256).hexdigest()
    req = Request(URL, headers={"X-PSC-Timestamp": stamp,
                                "X-PSC-Signature": signature,
                                "Accept": "application/json",
                                "Cache-Control": "no-store"})
    with urlopen(req, timeout=12) as response:
        document = json.loads(response.read(10000))
    if (not isinstance(document, dict) or document.get("state") != "OK"
            or not isinstance(document.get("encrypted_refresh_token"), str)):
        raise RuntimeError("PSC OAuth disk source unavailable")
    _write_cache(document["encrypted_refresh_token"])
    return True


def fetch_encrypted_refresh_token():
    """Calendar request path: ONLY read encrypted bytes from local cache."""
    try:
        value = CACHE.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise RuntimeError("Local PSC OAuth cache not ready") from None
    if not 100 <= len(value) <= 8192:
        raise RuntimeError("Local PSC OAuth cache invalid")
    return value


def save_encrypted_refresh_token(encrypted):
    """Write encrypted OAuth refresh token to disk, then refresh local cache."""
    if not isinstance(encrypted, str) or not 100 <= len(encrypted) <= 8192:
        raise ValueError("Invalid OAuth ciphertext")
    secret = _shared_secret()
    payload = json.dumps({"encrypted_refresh_token": encrypted},
                         separators=(",", ":")).encode()
    stamp = str(int(time.time()))
    message = (stamp + ".POST " + PATH + "."
               + hashlib.sha256(payload).hexdigest()).encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    req = Request(URL, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "X-PSC-Timestamp": stamp, "X-PSC-Signature": signature,
        "Accept": "application/json"})
    with urlopen(req, timeout=15) as response:
        result = json.loads(response.read(10000))
    if result.get("state") != "STORED":
        raise RuntimeError("PSC OAuth store did not acknowledge write")
    _write_cache(encrypted)
    return True
