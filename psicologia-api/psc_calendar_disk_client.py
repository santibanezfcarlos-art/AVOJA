"""Read signed OAuth ciphertext from PSC's existing persistent disk.

No plaintext OAuth refresh tokens leave the Calendar service.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.request import Request, urlopen

PATH = "/internal/psc/calendar/oauth-ciphertext"
URL = "https://psicologia-carlos-calendar-api.onrender.com" + PATH


def fetch_encrypted_refresh_token():
    secret = os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    if len(secret) < 40:
        raise RuntimeError("PSC signed disk bridge unavailable")
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
    return document["encrypted_refresh_token"]
