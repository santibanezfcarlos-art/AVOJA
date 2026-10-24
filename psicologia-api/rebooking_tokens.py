"""Private, one-use rescheduling links for confirmed psychology bookings.

A URL is a bearer credential: store only SHA-256 digests, never log or reveal
raw tokens. No patient account is required. This module does not reschedule a
calendar event: the eventual confirmation endpoint must do that atomically
with the booking update.
"""
import hashlib
import secrets
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
    """Called once after server-side payment+Calendar confirmation.

    Repeated notification attempts MUST reuse the existing link. Since only
    a digest is persisted, issue a token once and pass the raw token directly
    to the email queue; never reissue a link just to retry an email.
    """
    token = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO psychology_reschedule_links(order_id,token_sha256)
                    VALUES (%s,%s)""", (order_id, digest(token)))
    return token

def check(conn, token, *, now=None):
    if not isinstance(token, str) or len(token) > 128:
        return None, "invalid"
    row = conn.execute("""SELECT p.order_id,p.state,h.starts_at,l.redeemed_at,l.revoked_at,
                                  (SELECT COUNT(*) FROM psychology_reschedule_audit a
                                   WHERE a.order_id=p.order_id) AS changes
                           FROM psychology_reschedule_links l
                           JOIN psychology_demo_payments p ON p.order_id=l.order_id
                           JOIN psychology_booking_holds h ON h.id=p.hold_id
                           WHERE l.token_sha256=%s""", (digest(token),)).fetchone()
    if not row:
        return None, "invalid"
    if row["state"] != "CONFIRMED" or row["revoked_at"] or row["redeemed_at"] or row["changes"]:
        return None, "used"
    now = now or datetime.now(TZ)
    if row["starts_at"] - now < timedelta(hours=24):
        return None, "too_late"
    return row, "eligible"
