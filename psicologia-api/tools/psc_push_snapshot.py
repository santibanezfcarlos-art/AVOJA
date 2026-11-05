"""One-shot signed upload of encrypted PG snapshot to PSC's existing persistent disk.

No writes to PostgreSQL. Configure PSC_PG_SNAPSHOT_PUSH_ON_START=true ONLY
after the receiver is deployed and ready.
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

DEST = "https://psicologia-carlos-calendar-api.onrender.com/internal/psc/postgres-backup/upload"


def push_once():
    from tools.psc_pg_sqlite_snapshot import export_snapshot
    payload, metrics = export_snapshot()
    if len(payload) > 24 * 1024 * 1024:
        raise RuntimeError("Encrypted snapshot too large")
    ts = str(int(time.time()))
    name = "psc-pg-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".enc"
    secret = os.environ["PSICOLOGIA_DEMO_BRIDGE_KEY"]
    sig = hmac.new(secret.encode(), ts.encode() + b"." + name.encode() + b"." + payload,
                   hashlib.sha256).hexdigest()
    req = Request(
        DEST,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "X-PSC-Backup-Timestamp": ts,
            "X-PSC-Backup-Signature": sig,
            "X-PSC-Backup-Name": name
        }
    )
    with urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read(2048))
    if result.get("state") not in ("STORED", "ALREADY_PRESENT"):
        raise RuntimeError("Storage acknowledgement missing")
    ciphertext_sha = hashlib.sha256(payload).hexdigest()
    if result.get("sha256") != ciphertext_sha:
        raise RuntimeError("Remote file checksum mismatch")
    return {"state": "STORED_AND_SHA256_VERIFIED",
            "table_count": metrics["tables"],
            "row_count": sum(metrics["counts"].values()),
            "ciphertext_sha256": ciphertext_sha}
