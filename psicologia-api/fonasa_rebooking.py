"""Fonasa data adapter for the existing shared rescheduling engine.

No payment is fabricated. The confirmed Calendar event keeps its deterministic
hold ID. Private links are hashed and can be redeemed only once.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from booking_holds import _lock_day, TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS psychology_fonasa_bookings (
 source_id varchar(40) PRIMARY KEY,
 hold_id uuid NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
 patient_name varchar(120) NOT NULL,
 patient_email varchar(254) NOT NULL,
 token_sha256 char(64) NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 redeemed_at timestamptz,
 old_starts_at timestamptz,
 new_starts_at timestamptz
);
"""

def register(conn, source, hold_id, name, email, start, end):
    key=os.environ.get('PSICOLOGIA_REBOOKING_TOKEN_SECRET','')
    if len(key)<40:
        raise RuntimeError('Rebooking not configured')
    token=hmac.new(key.encode(),('fonasa-rebooking-v1:'+source).encode(),hashlib.sha256).hexdigest()
    conn.execute(SCHEMA)
    _lock_day(conn,start.date())
    conn.execute("""INSERT INTO psychology_booking_holds
       (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,expires_at)
       VALUES (%s,%s,%s,%s,%s,'psicoterapia','CONFIRMED',%s)
       ON CONFLICT(id) DO NOTHING""",(hold_id,hashlib.sha256(secrets.token_bytes(32)).hexdigest(),start.date(),start,end,end))
    saved=conn.execute('SELECT starts_at,ends_at,status FROM psychology_booking_holds WHERE id=%s',(hold_id,)).fetchone()
    if not saved or saved['starts_at']!=start or saved['ends_at']!=end or saved['status']!='CONFIRMED':
        raise RuntimeError('Booking state mismatch')
    conn.execute("""INSERT INTO psychology_fonasa_bookings
       (source_id,hold_id,patient_name,patient_email,token_sha256)
       VALUES (%s,%s,%s,%s,%s) ON CONFLICT(source_id) DO NOTHING""",
       (source,hold_id,name,email,hashlib.sha256(token.encode()).hexdigest()))
    row=conn.execute('SELECT token_sha256,patient_email FROM psychology_fonasa_bookings WHERE source_id=%s',(source,)).fetchone()
    if not row or not hmac.compare_digest(row['token_sha256'],hashlib.sha256(token.encode()).hexdigest()) or row['patient_email']!=email:
        raise RuntimeError('Stored link mismatch')
    return token

def exists(conn):
    return conn.execute("SELECT to_regclass('public.psychology_fonasa_bookings') IS NOT NULL AS ready").fetchone()['ready']

def lookup(conn, token, *, lock=False):
    if not exists(conn):return None
    suffix=' FOR UPDATE OF f,h' if lock else ''
    return conn.execute("""SELECT f.source_id AS order_id,f.hold_id,f.patient_name,
       f.patient_email AS test_recipient,f.redeemed_at,h.service,h.slot_date,
       h.starts_at,h.ends_at,h.status AS state,'fonasa' AS source
       FROM psychology_fonasa_bookings f JOIN psychology_booking_holds h ON h.id=f.hold_id
       WHERE f.token_sha256=%s"""+suffix,(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()

def eligible(row, *, now=None):
    if not row:return None,'invalid'
    if row['state']!='CONFIRMED' or row['redeemed_at']:return None,'used'
    if row['starts_at']-(now or datetime.now(TZ))<timedelta(hours=24):return None,'too_late'
    return row,'eligible'

def booking(conn, source, *, lock=False):
    suffix=' FOR UPDATE OF f,h' if lock else ''
    return conn.execute("""SELECT f.source_id AS order_id,f.hold_id,f.patient_name,
       f.patient_email AS test_recipient,h.service,h.slot_date,h.starts_at,h.ends_at,
       h.status AS state,'fonasa' AS source FROM psychology_fonasa_bookings f
       JOIN psychology_booking_holds h ON h.id=f.hold_id WHERE f.source_id=%s"""+suffix,(source,)).fetchone()

def redeem(conn, source, old_start, new_start):
    result=conn.execute("""UPDATE psychology_fonasa_bookings SET redeemed_at=now(),
       old_starts_at=%s,new_starts_at=%s WHERE source_id=%s AND redeemed_at IS NULL""",(old_start,new_start,source))
    if result.rowcount!=1:raise RuntimeError('Rebooking already used')
