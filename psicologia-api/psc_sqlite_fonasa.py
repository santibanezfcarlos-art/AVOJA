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
        row=c.execute("""SELECT f.source_id,f.hold_id,f.patient_name,f.patient_email,
                   f.redeemed_at,h.slot_date,h.starts_at,h.ends_at,h.service,h.status
            FROM psychology_fonasa_bookings f
            JOIN psychology_booking_holds h ON h.id=f.hold_id
            WHERE f.token_sha256=?""",(token_sha256,)).fetchone()
        if not row:return None
        return dict(row)

def eligibility(*,token_sha256,test_path=None,now=None):
    row=lookup(token_sha256=token_sha256,test_path=test_path)
    if not row:return "NOT_FOUND"
    if row["redeemed_at"] or row["status"]!="CONFIRMED":return "NOT_ELIGIBLE"
    with connect(test_path=test_path) as c:
        claim=c.execute("SELECT state FROM psc_fonasa_rebook_claims WHERE source_id=?",
                        (row["source_id"],)).fetchone() if (
                        c.execute("SELECT 1 FROM sqlite_master WHERE name='psc_fonasa_rebook_claims'").fetchone()) else None
    if claim:return "PENDING_RECONCILIATION"
    if utc(row["starts_at"])-utc(now or datetime.now(timezone.utc)).total_seconds()<86400:
        return "TOO_LATE"
    return "ELIGIBLE"

# Deliberately separate from billing and from the eight imported PG tables.
# PREPARED claims survive restarts and remain unavailable until Calendar
# outcome is explicitly reconciled. Never auto-expire an uncertain Calendar move.
CLAIMS_SCHEMA = """CREATE TABLE IF NOT EXISTS psc_fonasa_rebook_claims(
    source_id TEXT PRIMARY KEY REFERENCES psychology_fonasa_bookings(source_id),
    old_starts_at TEXT NOT NULL,old_ends_at TEXT NOT NULL,
    new_starts_at TEXT NOT NULL,new_ends_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PREPARED','COMPLETED')),
    created_at TEXT NOT NULL,completed_at TEXT
)"""


def prepare_change(*,source_id,token_sha256,new_start,new_end,test_path=None,
                   now=None,max_daily=3):
    """Atomically claim a destination BEFORE touching Google Calendar.

    In-progress destinations block competing changes until a human or a
    verified reconciliation resolves uncertain Calendar outcomes.
    """
    from psc_sqlite_state import ACTIVE
    from zoneinfo import ZoneInfo
    now=utc(now or datetime.now(timezone.utc))
    new_start,new_end=utc(new_start),utc(new_end)
    day=new_start.astimezone(ZoneInfo("America/Santiago")).date()
    if (new_start<=now or new_start>=new_end or
        new_end.astimezone(ZoneInfo("America/Santiago")).date()!=day):
        raise ValueError("Invalid target interval")
    with connect(write=True,test_path=test_path) as c:
        c.execute(CLAIMS_SCHEMA)
        r=c.execute("""SELECT f.source_id,f.redeemed_at,f.token_sha256,
               h.id AS hold_id,h.status,h.starts_at,h.ends_at
               FROM psychology_fonasa_bookings f
               JOIN psychology_booking_holds h ON h.id=f.hold_id
               WHERE f.source_id=?""",(source_id,)).fetchone()
        if not r or not hmac.compare_digest(r["token_sha256"],token_sha256):
            raise FonasaStateConflict("No matching Fonasa booking")
        old_start,old_end=utc(r["starts_at"]),utc(r["ends_at"])
        if (r["redeemed_at"] or r["status"]!="CONFIRMED" or
            (old_start-now).total_seconds()<86400 or old_start==new_start):
            raise FonasaStateConflict("Not eligible for rebooking")
        existing=c.execute("SELECT * FROM psc_fonasa_rebook_claims WHERE source_id=?",
                           (source_id,)).fetchone()
        if existing:
            if (existing["state"]=="PREPARED" and
                utc(existing["new_starts_at"])==new_start and
                utc(existing["new_ends_at"])==new_end):
                return "PREPARED"
            raise FonasaStateConflict("Pending or completed move exists")
        holds=c.execute("""SELECT id,starts_at,ends_at,status,expires_at
             FROM psychology_booking_holds WHERE slot_date=?""",(day.isoformat(),)).fetchall()
        busy=[(utc(x["starts_at"]),utc(x["ends_at"])) for x in holds
              if str(x["id"])!=str(r["hold_id"]) and
              (x["status"]=="CONFIRMED" or
               (x["status"] in ACTIVE and utc(x["expires_at"])>now))]
        claims=c.execute("""SELECT q.source_id,q.new_starts_at,q.new_ends_at
             FROM psc_fonasa_rebook_claims q
             WHERE q.state='PREPARED'""").fetchall()
        busy.extend((utc(q["new_starts_at"]),utc(q["new_ends_at"]))
                    for q in claims if q["source_id"]!=source_id and
                    utc(q["new_starts_at"]).astimezone(ZoneInfo("America/Santiago")).date()==day)
        if len(busy)>=max_daily or any(new_start<b and a<new_end for a,b in busy):
            raise FonasaStateConflict("Destination already occupied")
        c.execute("""INSERT INTO psc_fonasa_rebook_claims
            (source_id,old_starts_at,old_ends_at,new_starts_at,new_ends_at,state,created_at)
            VALUES (?,?,?,?,?,'PREPARED',?)""",
            (source_id,old_start.isoformat(),old_end.isoformat(),
             new_start.isoformat(),new_end.isoformat(),now.isoformat()))
    return "PREPARED"


def commit_change(*,source_id,token_sha256,new_start,new_end,test_path=None):
    """Commit ONLY after the caller independently verified Google Calendar.

    A lost reply is idempotent and safely returns CONFIRMED on repetition.
    """
    from zoneinfo import ZoneInfo
    new_start,new_end=utc(new_start),utc(new_end)
    with connect(write=True,test_path=test_path) as c:
        claim=c.execute("""SELECT q.*,f.hold_id,f.token_sha256,f.redeemed_at
            FROM psc_fonasa_rebook_claims q
            JOIN psychology_fonasa_bookings f USING(source_id)
            WHERE q.source_id=?""",(source_id,)).fetchone()
        if not claim or not hmac.compare_digest(claim["token_sha256"],token_sha256):
            raise FonasaStateConflict("Claim not found")
        if utc(claim["new_starts_at"])!=new_start or utc(claim["new_ends_at"])!=new_end:
            raise FonasaStateConflict("Claim target mismatch")
        if claim["state"]=="COMPLETED" and claim["redeemed_at"]:
            return "CONFIRMED"
        if claim["state"]!="PREPARED" or claim["redeemed_at"]:
            raise FonasaStateConflict("Claim already used")
        stamp=datetime.now(timezone.utc).isoformat()
        day=new_start.astimezone(ZoneInfo("America/Santiago")).date().isoformat()
        c.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES(?)",(day,))
        changed=c.execute("""UPDATE psychology_booking_holds
           SET starts_at=?,ends_at=?,slot_date=?,updated_at=?
           WHERE id=? AND status='CONFIRMED'""",
           (new_start.isoformat(),new_end.isoformat(),day,stamp,str(claim["hold_id"])))
        if changed.rowcount!=1:
            raise FonasaStateConflict("Calendar hold state unexpected")
        c.execute("""UPDATE psychology_fonasa_bookings
           SET redeemed_at=?,old_starts_at=?,new_starts_at=?
           WHERE source_id=? AND redeemed_at IS NULL""",
           (stamp,claim["old_starts_at"],new_start.isoformat(),source_id))
        c.execute("""UPDATE psc_fonasa_rebook_claims
           SET state='COMPLETED',completed_at=?
           WHERE source_id=? AND state='PREPARED'""",(stamp,source_id))
    return "CONFIRMED"


def busy_intervals(*,day,exclude_hold_id=None,test_path=None,now=None):
    """Read own active holds and PREPARED target claims, without PII."""
    from psc_sqlite_state import ACTIVE
    from zoneinfo import ZoneInfo
    now=utc(now or datetime.now(timezone.utc))
    with connect(test_path=test_path) as c:
        holds=c.execute("""SELECT id,starts_at,ends_at,status,expires_at
          FROM psychology_booking_holds WHERE slot_date=?""",(day.isoformat(),)).fetchall()
        claims=(c.execute("""SELECT new_starts_at,new_ends_at
          FROM psc_fonasa_rebook_claims WHERE state='PREPARED'""").fetchall()
          if c.execute("SELECT 1 FROM sqlite_master WHERE name='psc_fonasa_rebook_claims'").fetchone()
          else [])
    windows=[(utc(r["starts_at"]),utc(r["ends_at"])) for r in holds
        if str(r["id"])!=str(exclude_hold_id) and
        (r["status"]=="CONFIRMED" or (r["status"] in ACTIVE and utc(r["expires_at"])>now))]
    windows.extend((utc(q["new_starts_at"]),utc(q["new_ends_at"])) for q in claims
        if utc(q["new_starts_at"]).astimezone(ZoneInfo("America/Santiago")).date()==day)
    return windows


def cancel_deleted(*,source_id,hold_id,test_path=None):
    """Call only after the pilot verified that its Google event was deleted."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{15,40}",str(source_id)):
        raise ValueError("Invalid source")
    with connect(test_path=test_path,write=True) as c:
        row=c.execute("SELECT hold_id FROM psychology_fonasa_bookings WHERE source_id=?",
                      (source_id,)).fetchone()
        if row and str(row["hold_id"])!=str(hold_id):
            raise FonasaStateConflict("Hold does not match source")
        # Never release a slot with an unresolved Calendar move.
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='psc_fonasa_rebook_claims'").fetchone():
            unresolved=c.execute("""SELECT 1 FROM psc_fonasa_rebook_claims
                WHERE source_id=? AND state='PREPARED'""",(source_id,)).fetchone()
            if unresolved:raise FonasaStateConflict("Rebooking needs reconciliation")
        stamp=datetime.now(timezone.utc).isoformat()
        c.execute("""UPDATE psychology_fonasa_bookings SET redeemed_at=COALESCE(redeemed_at,?)
            WHERE source_id=?""",(stamp,source_id))
        c.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=?
            WHERE id=? AND status='CONFIRMED'""",(stamp,str(hold_id)))
    return "RESET"


def abort_prepared_change(*,source_id,token_sha256,new_start,new_end,test_path=None):
    """Abort ONLY after the caller verified original Google event still exists.

    Without that verification an ambiguous move must remain PREPARED, so both
    the old hold and destination stay unavailable pending reconciliation.
    """
    new_start,new_end=utc(new_start),utc(new_end)
    with connect(test_path=test_path,write=True) as c:
        row=c.execute("""SELECT q.state,q.new_starts_at,q.new_ends_at,
                f.token_sha256 FROM psc_fonasa_rebook_claims q
                JOIN psychology_fonasa_bookings f USING(source_id)
                WHERE q.source_id=?""",(source_id,)).fetchone()
        if not row:return "NOTHING_TO_ABORT"
        if not hmac.compare_digest(row["token_sha256"],token_sha256):
            raise FonasaStateConflict("Token mismatch")
        if row["state"]!="PREPARED" or utc(row["new_starts_at"])!=new_start or utc(row["new_ends_at"])!=new_end:
            raise FonasaStateConflict("Claim not abortable")
        c.execute("""DELETE FROM psc_fonasa_rebook_claims
                     WHERE source_id=? AND state='PREPARED'""",(source_id,))
    return "ABORTED"
