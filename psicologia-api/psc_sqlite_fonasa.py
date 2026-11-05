"""Fonasa lifecycle in PSC's local SQLite state; no payment calls.

Source is signed by the already-existing pilot bridge, and no plaintext token
is persisted. This module is STAGED only; do not route live traffic until
the Google Calendar reschedule transaction and recovery are validated.
"""
import hashlib
import hmac
import re
import sqlite3
from datetime import datetime,timezone
from psc_sqlite_state import connect,utc,StateUnavailable

class FonasaStateConflict(RuntimeError): pass

def register(*,source_id,hold_id,token_sha256,name,email,start,end,test_path=None):
    if not isinstance(source_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{15,40}",source_id):
        raise ValueError("Invalid source ID")
    if not re.fullmatch(r"[0-9a-f-]{36}",str(hold_id)) or not re.fullmatch(r"[0-9a-f]{64}",token_sha256):
        raise ValueError("Invalid hold or token")
    if not isinstance(name,str) or not 1<=len(name)<=120:
        raise ValueError("Invalid name")
    if not isinstance(email,str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email) or len(email)>254:
        raise ValueError("Invalid email")
    start,end=utc(start),utc(end)
    if start>=end:raise ValueError("Invalid booking interval")
    from zoneinfo import ZoneInfo
    day=start.astimezone(ZoneInfo("America/Santiago")).date()
    if end.astimezone(ZoneInfo("America/Santiago")).date()!=day:
        raise ValueError("Booking spans multiple dates")
    with connect(write=True,test_path=test_path) as c:
        c.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES(?)",(day.isoformat(),))
        hold=c.execute("SELECT starts_at,ends_at,status FROM psychology_booking_holds WHERE id=?",(str(hold_id),)).fetchone()
        if hold:
            if (utc(hold["starts_at"])!=start or utc(hold["ends_at"])!=end or
                    hold["status"]!="CONFIRMED"):
                raise FonasaStateConflict("Existing hold differs from Calendar")
        else:
            # Caller must FIRST verify the deterministic event in Google Calendar.
            digest=hashlib.sha256(("fonasa:"+source_id).encode()).hexdigest()
            now=datetime.now(timezone.utc).isoformat()
            c.execute("""INSERT INTO psychology_booking_holds
                (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,expires_at,created_at,updated_at)
                VALUES (?,?,?,?,?,'psicoterapia','CONFIRMED',?,?,?)""",
                (str(hold_id),digest,day.isoformat(),start.isoformat(),end.isoformat(),
                 end.isoformat(),now,now))
        booked=c.execute("""SELECT source_id,hold_id,token_sha256,patient_email,redeemed_at
            FROM psychology_fonasa_bookings WHERE source_id=?""",(source_id,)).fetchone()
        if booked:
            if (str(booked["hold_id"])!=str(hold_id) or
                not hmac.compare_digest(booked["token_sha256"],token_sha256) or
                booked["patient_email"].lower()!=email.lower()):
                raise FonasaStateConflict("Existing Fonasa booking differs")
        else:
            c.execute("""INSERT INTO psychology_fonasa_bookings
              (source_id,hold_id,patient_name,patient_email,token_sha256,created_at)
              VALUES (?,?,?,?,?,?)""",
              (source_id,str(hold_id),name,email,token_sha256,
               datetime.now(timezone.utc).isoformat()))
    return "REGISTERED"

def lookup(*,token_sha256,test_path=None):
    if not isinstance(token_sha256,str) or not re.fullmatch(r"[0-9a-f]{64}",token_sha256):
        return None
    with connect(test_path=test_path) as c:
        row=c.execute("""SELECT f.source_id,f.hold_id,f.redeemed_at,h.starts_at,h.ends_at,h.status
            FROM psychology_fonasa_bookings f
            JOIN psychology_booking_holds h ON h.id=f.hold_id
            WHERE f.token_sha256=?""",(token_sha256,)).fetchone()
        if not row:return None
        return dict(row)

def eligibility(*,token_sha256,test_path=None,now=None):
    row=lookup(token_sha256=token_sha256,test_path=test_path)
    if not row:return "NOT_FOUND"
    if row["redeemed_at"] or row["status"]!="CONFIRMED":return "NOT_ELIGIBLE"
    if utc(row["starts_at"])-utc(now or datetime.now(timezone.utc)).total_seconds()<86400:
        return "TOO_LATE"
    return "ELIGIBLE"
