"""Private, one-use rescheduling links for confirmed psychology bookings.

A URL is a bearer credential: store only SHA-256 digests, never log or reveal
raw tokens. No patient account is required. This module does not reschedule a
calendar event: the eventual confirmation endpoint must do that atomically
with the booking update.
"""
import hashlib
import hmac
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
TOKEN_SCHEMA = """
CREATE TABLE IF NOT EXISTS psychology_reschedule_links (
 order_id varchar(26) PRIMARY KEY REFERENCES psychology_demo_payments(order_id),
 token_sha256 char(64) NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 redeemed_at timestamptz,
 revoked_at timestamptz
);
CREATE TABLE IF NOT EXISTS psychology_reschedule_audit (
 id bigserial PRIMARY KEY,
 order_id varchar(26) NOT NULL REFERENCES psychology_demo_payments(order_id),
 old_starts_at timestamptz NOT NULL,
 new_starts_at timestamptz NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT now(),
 channel varchar(20) NOT NULL DEFAULT 'patient_link',
 CONSTRAINT one_completed_reschedule UNIQUE (order_id)
);
"""

def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def issue(conn, order_id):
    """Derive a stable private link so retries do not rotate it."""
    key = os.environ.get("PSICOLOGIA_REBOOKING_TOKEN_SECRET", "")
    if len(key) < 40:
        raise RuntimeError("Rebooking not configured")
    token = hmac.new(key.encode(), ("rebooking-v1:" + order_id).encode(),
                     hashlib.sha256).hexdigest()
    hashed = digest(token)
    conn.execute("INSERT INTO psychology_reschedule_links(order_id,token_sha256) VALUES (%s,%s) ON CONFLICT(order_id) DO NOTHING", (order_id,hashed))
    stored = conn.execute("SELECT token_sha256 FROM psychology_reschedule_links WHERE order_id=%s", (order_id,)).fetchone()
    if not stored or not hmac.compare_digest(stored["token_sha256"],hashed):
        raise RuntimeError("Stored link mismatch")
    return token

def check(conn, token, *, now=None):
    if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
        return None, "invalid"
    row = conn.execute("""SELECT p.order_id,p.state,h.starts_at,l.redeemed_at,l.revoked_at,
                                  (SELECT COUNT(*) FROM psychology_reschedule_audit a
                                   WHERE a.order_id=p.order_id) AS changes
                           FROM psychology_reschedule_links l
                           JOIN psychology_demo_payments p ON p.order_id=l.order_id
                           JOIN psychology_booking_holds h ON h.id=p.hold_id
                           WHERE l.token_sha256=%s""", (digest(token),)).fetchone()
    if not row:
        from fonasa_rebooking import lookup, eligible
        return eligible(lookup(conn,token),now=now)
    if row["state"] != "CONFIRMED" or row["revoked_at"] or row["redeemed_at"] or row["changes"]:
        return None, "used"
    now = now or datetime.now(TZ)
    if row["starts_at"] - now < timedelta(hours=24):
        return None, "too_late"
    return row, "eligible"
