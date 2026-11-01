"""Transactional PSC hold storage for a FUTURE, explicitly authorized cutover.

Not wired to the public agenda, payments, emails, or Fonasa. Operates only on a
dedicated SQLite copy (never the existing billing ledger). All mutations use
BEGIN IMMEDIATE for single-writer serialization and CHECK/UNIQUE constraints
from the PG->SQLite migration. Use time-aware UTC timestamps to avoid string
ordering bugs between "+00:00" and other offsets.

Security: no patient names/tokens in errors or logs; no external side effects.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import hmac
from pathlib import Path
import secrets
import sqlite3
import uuid
from zoneinfo import ZoneInfo

from psc_holds_sqlite import configured_file

TZ = ZoneInfo("America/Santiago")
ACTIVE = frozenset(("HELD", "PAYMENT_PENDING", "RECONCILIATION_REQUIRED"))
ALLOWED = frozenset(("psicoterapia", "empleabilidad", "competencias"))
MAX_DAILY = 3
HOLD_TTL = timedelta(minutes=10)
PAYMENT_TTL = timedelta(minutes=20)


class StorageConflict(Exception):
    """Caller must fail closed and return a non-sensitive conflict status."""


class StorageUnavailable(Exception):
    """Caller must fail closed; do not initiate checkout or a Calendar write."""


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(timezone.utc)


def _read_utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise StorageUnavailable("Timezone missing in storage")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return _utc(value).isoformat()


def _day(date_or_str):
    if isinstance(date_or_str, str):
        value = date.fromisoformat(date_or_str)
    else:
        value = date_or_str
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("Date required")
    return value.isoformat()


def _overlap(start, end, old_start, old_end):
    return start < old_end and old_start < end


class PSCStore:
    def __init__(self, db_path=None):
        self.path = Path(db_path) if db_path is not None else configured_file()
        if not self.path.is_absolute() or not self.path.is_file() or self.path.is_symlink():
            raise StorageUnavailable("Dedicated migrated database missing")

    def _connect(self):
        # fail closed instead of implicitly creating a new empty SQLite file
        connection = sqlite3.connect(
            self.path.as_uri() + "?mode=rw", uri=True, timeout=6,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=6000")
        return connection

    @staticmethod
    def _active(conn, day, now):
        records = conn.execute(
            """SELECT starts_at,ends_at,status,expires_at
               FROM psychology_booking_holds WHERE slot_date=?""",
            (day,),
        ).fetchall()
        windows = []
        for row in records:
            start, end = _read_utc(row["starts_at"]), _read_utc(row["ends_at"])
            expires = _read_utc(row["expires_at"])
            if row["status"] == "CONFIRMED" or (row["status"] in ACTIVE and expires > now):
                windows.append((start, end))
        return windows

    @staticmethod
    def _ensure_available(conn, day, start, end, now, *, exclude_hold=None):
        query = """SELECT id,starts_at,ends_at,status,expires_at
                   FROM psychology_booking_holds WHERE slot_date=?"""
        rows = conn.execute(query, (day,)).fetchall()
        count = 0
        for row in rows:
            if exclude_hold and row["id"] == exclude_hold:
                continue
            if (row["status"] != "CONFIRMED" and
                (row["status"] not in ACTIVE or _read_utc(row["expires_at"]) <= now)):
                continue
            count += 1
            if _overlap(start, end, _read_utc(row["starts_at"]), _read_utc(row["ends_at"])):
                raise StorageConflict("Slot already occupied")
        if count >= MAX_DAILY:
            raise StorageConflict("Daily capacity reached")

    def reserve(self, service, day, start, end, *, now=None, hold_id=None, secret=None):
        if service not in ALLOWED:
            raise ValueError("Invalid service")
        today = _day(day)
        start, end = _utc(start), _utc(end)
        moment = _utc(now or datetime.now(timezone.utc))
        if (end <= start or start <= moment or start.astimezone(TZ).date().isoformat() != today
                or end.astimezone(TZ).date().isoformat() != today):
            raise ValueError("Invalid appointment interval")
        identifier = str(uuid.UUID(str(hold_id))) if hold_id else str(uuid.uuid4())
        token = secret or secrets.token_urlsafe(32)
        digest = sha256(token.encode("utf-8")).hexdigest()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES (?)", (today,))
                self._ensure_available(db, today, start, end, moment)
                db.execute(
                    """INSERT INTO psychology_booking_holds
                    (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,expires_at,
                     created_at,updated_at)
                    VALUES (?,?,?,?,?,?,'HELD',?,?,?)""",
                    (identifier, digest, today, _iso(start), _iso(end), service,
                     _iso(moment + HOLD_TTL), _iso(moment), _iso(moment)),
                )
            return {"id":identifier, "cancel_secret":token, "status":"HELD"}
        except sqlite3.Error:
            raise StorageUnavailable("Failed to save hold") from None

    def cancel(self, identifier, secret, *, now=None):
        hold_id = str(uuid.UUID(str(identifier)))
        if not isinstance(secret, str) or not secret or len(secret) > 150:
            raise ValueError("Invalid cancellation credentials")
        digest = sha256(secret.encode("utf-8")).hexdigest()
        moment = _utc(now or datetime.now(timezone.utc))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT cancel_secret_sha256,status,expires_at FROM psychology_booking_holds WHERE id=?",
                                 (hold_id,)).fetchone()
                if not row:
                    raise StorageConflict("Unknown hold")
                if not hmac.compare_digest(row["cancel_secret_sha256"], digest):
                    raise StorageConflict("Invalid cancellation credentials")
                if row["status"] == "CANCELLED":
                    return "CANCELLED"
                if row["status"] != "HELD" or _read_utc(row["expires_at"]) <= moment:
                    raise StorageConflict("Hold cannot be cancelled")
                db.execute(
                    "UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=? WHERE id=?",
                    (_iso(moment),hold_id),
                )
                return "CANCELLED"
        except sqlite3.Error:
            raise StorageUnavailable("Hold cancellation failed") from None

    def mark_payment_pending(self, identifier, order_id, session_id, service, amount, email,
                             *, now=None, patient_name=None):
        """Atomic payment hold extension and order metadata staging.

        DOES NOT initiate a charge or trust an external payment. Callers must
        validate catalog price, email consent and HMAC before calling.
        """
        hold_id = str(uuid.UUID(str(identifier)))
        moment = _utc(now or datetime.now(timezone.utc))
        if (service not in ALLOWED or type(amount) is not int or amount <= 0
                or not isinstance(order_id,str) or not order_id
                or not isinstance(session_id,str) or not session_id
                or not isinstance(email,str) or "@" not in email):
            raise ValueError("Invalid pending payment")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT hold_id,session_id,amount_clp,state FROM psychology_demo_payments WHERE order_id=?",
                                   (order_id,)).fetchone()
                if prior:
                    if (prior["hold_id"] != hold_id or prior["session_id"] != session_id
                            or prior["amount_clp"] != amount
                            or prior["state"] not in ("PAYMENT_PENDING","AUTHORIZED","CALENDAR_PENDING","CONFIRMED")):
                        raise StorageConflict("Existing order does not match")
                    return prior["state"]
                held = db.execute(
                    "SELECT service,status,expires_at FROM psychology_booking_holds WHERE id=?",
                    (hold_id,),
                ).fetchone()
                if (not held or held["service"] != service or held["status"] != "HELD"
                        or _read_utc(held["expires_at"]) <= moment):
                    raise StorageConflict("Hold no longer valid")
                db.execute(
                    """INSERT INTO psychology_demo_payments
                    (order_id,hold_id,session_id,service,amount_clp,test_recipient,patient_name,
                     state,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,'PAYMENT_PENDING',?,?)""",
                    (order_id,hold_id,session_id,service,amount,email.lower(),patient_name,
                     _iso(moment),_iso(moment)),
                )
                db.execute(
                    """UPDATE psychology_booking_holds SET status='PAYMENT_PENDING',
                        expires_at=?,updated_at=? WHERE id=? AND status='HELD'""",
                    (_iso(moment+PAYMENT_TTL),_iso(moment),hold_id),
                )
                return "PAYMENT_PENDING"
        except sqlite3.IntegrityError:
            raise StorageConflict("Duplicate payment reservation") from None
        except sqlite3.Error:
            raise StorageUnavailable("Payment reservation failed") from None

    def complete_payment(self, order_id, session_id, amount, outcome, *, now=None):
        """Commit payment provider's verified outcome; no Calendar side effect.

        An expired hold never becomes CONFIRMED automatically. If approved after
        expiry, mark RECONCILIATION_REQUIRED and fail closed for external work.
        """
        if outcome not in ("approved","rejected","cancelled"):
            raise ValueError("Unknown payment outcome")
        moment = _utc(now or datetime.now(timezone.utc))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    """SELECT p.hold_id,p.state,p.session_id,p.amount_clp,h.status AS hold_state,
                       h.expires_at
                       FROM psychology_demo_payments p
                       JOIN psychology_booking_holds h ON p.hold_id=h.id
                       WHERE p.order_id=?""", (order_id,)
                ).fetchone()
                if (not row or row["session_id"] != session_id
                        or row["amount_clp"] != amount):
                    raise StorageConflict("Payment reference mismatch")
                state = row["state"]
                if outcome in ("rejected","cancelled"):
                    if state in ("AUTHORIZED","CALENDAR_PENDING","CONFIRMED","RECONCILIATION_REQUIRED"):
                        raise StorageConflict("Cannot cancel authorized payment")
                    next_state = "REJECTED" if outcome=="rejected" else "CANCELLED"
                    db.execute("UPDATE psychology_demo_payments SET state=?,updated_at=? WHERE order_id=?",
                               (next_state,_iso(moment),order_id))
                    db.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=?
                                  WHERE id=? AND status IN ('HELD','PAYMENT_PENDING')""",
                               (_iso(moment),row["hold_id"]))
                    return next_state
                if state in ("AUTHORIZED","CALENDAR_PENDING","CONFIRMED","RECONCILIATION_REQUIRED"):
                    return state
                if state != "PAYMENT_PENDING":
                    raise StorageConflict("Unexpected payment state")
                if row["hold_state"] != "PAYMENT_PENDING" or _read_utc(row["expires_at"]) <= moment:
                    db.execute("UPDATE psychology_demo_payments SET state='RECONCILIATION_REQUIRED',verified_at=?,updated_at=? WHERE order_id=?",
                               (_iso(moment),_iso(moment),order_id))
                    db.execute("UPDATE psychology_booking_holds SET status='RECONCILIATION_REQUIRED',updated_at=? WHERE id=? AND status='PAYMENT_PENDING'",
                               (_iso(moment),row["hold_id"]))
                    return "RECONCILIATION_REQUIRED"
                db.execute("UPDATE psychology_demo_payments SET state='AUTHORIZED',verified_at=?,updated_at=? WHERE order_id=?",
                           (_iso(moment),_iso(moment),order_id))
                return "AUTHORIZED"
        except sqlite3.Error:
            raise StorageUnavailable("Payment reconciliation failed") from None

    def register_fonasa(self, source_id, hold_id, patient_name, patient_email,
                        token_sha256, start, end, *, now=None):
        """Idempotently preserve the deterministic event/hold identity.

        Caller must verify that the Google event exists and is owned BEFORE
        registering; this method does not contact Google or send emails.
        """
        if (not isinstance(source_id, str) or not 1 <= len(source_id) <= 40
                or not isinstance(patient_name, str) or not 2 <= len(patient_name.strip()) <= 120
                or not isinstance(patient_email, str) or not 3 <= len(patient_email) <= 254
                or "@" not in patient_email
                or not isinstance(token_sha256, str) or len(token_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in token_sha256)):
            raise ValueError("Invalid Fonasa registration")
        identity = str(uuid.UUID(str(hold_id)))
        start, end = _utc(start), _utc(end)
        moment = _utc(now or datetime.now(timezone.utc))
        day = start.astimezone(TZ).date().isoformat()
        if (start <= moment or end <= start or end.astimezone(TZ).date().isoformat() != day):
            raise ValueError("Invalid Fonasa appointment")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    """SELECT f.hold_id,f.patient_name,f.patient_email,f.token_sha256,
                              h.starts_at,h.ends_at,h.status
                       FROM psychology_fonasa_bookings f
                       JOIN psychology_booking_holds h ON h.id=f.hold_id
                       WHERE f.source_id=?""", (source_id,),
                ).fetchone()
                if previous:
                    if (previous["hold_id"] != identity or
                        previous["patient_name"] != patient_name or
                        previous["patient_email"] != patient_email or
                        not hmac.compare_digest(previous["token_sha256"],token_sha256) or
                        _read_utc(previous["starts_at"]) != start or
                        _read_utc(previous["ends_at"]) != end or
                        previous["status"] != "CONFIRMED"):
                        raise StorageConflict("Fonasa booking already differs")
                    return "CONFIRMED"
                # Existing hold id without source would break Calendar event
                # ownership/idempotency; reject rather than silently overwrite.
                if db.execute("SELECT 1 FROM psychology_booking_holds WHERE id=?", (identity,)).fetchone():
                    raise StorageConflict("Unexpected existing Fonasa hold")
                self._ensure_available(db, day, start, end, moment)
                db.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES (?)",(day,))
                db.execute(
                    """INSERT INTO psychology_booking_holds
                       (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,
                        expires_at,created_at,updated_at)
                       VALUES (?,?,?,?,?,'psicoterapia','CONFIRMED',?,?,?)""",
                    (identity,sha256(secrets.token_bytes(32)).hexdigest(),day,
                     _iso(start),_iso(end),_iso(end),_iso(moment),_iso(moment))
                )
                db.execute(
                    """INSERT INTO psychology_fonasa_bookings
                       (source_id,hold_id,patient_name,patient_email,token_sha256,created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (source_id,identity,patient_name,patient_email,token_sha256,_iso(moment))
                )
                return "CONFIRMED"
        except sqlite3.IntegrityError:
            raise StorageConflict("Duplicate Fonasa reference") from None
        except sqlite3.Error:
            raise StorageUnavailable("Fonasa registration unavailable") from None

    def fonasa_by_token(self, token, *, now=None):
        """Read state without using raw token as a query string or log output."""
        if not isinstance(token,str) or not token or len(token) > 160:
            raise ValueError("Invalid token")
        digest = sha256(token.encode()).hexdigest()
        try:
            with self._connect() as db:
                row = db.execute(
                    """SELECT f.source_id,f.hold_id,f.redeemed_at,
                              h.starts_at,h.ends_at,h.status
                       FROM psychology_fonasa_bookings f
                       JOIN psychology_booking_holds h ON h.id=f.hold_id
                       WHERE f.token_sha256=?""", (digest,)
                ).fetchone()
                if not row:
                    return {"state":"INVALID"}
                moment = _utc(now or datetime.now(timezone.utc))
                if row["status"]!="CONFIRMED" or row["redeemed_at"]:
                    state="USED"
                elif _read_utc(row["starts_at"])-moment < timedelta(hours=24):
                    state="TOO_LATE"
                else:
                    state="ELIGIBLE"
                # Does not emit patient name, email or token hash.
                return {"state":state,"source_id":row["source_id"],
                        "hold_id":row["hold_id"]}
        except sqlite3.Error:
            raise StorageUnavailable("Fonasa lookup unavailable") from None

    def finalize_fonasa_reschedule(self, source_id, old_start, new_start,
                                    new_end, *, now=None):
        """Persist a one-time reschedule AFTER Google confirmed the new event.

        On conflict or write failure, the caller must restore the Google event
        to old_start and report reconciliation if that compensation fails.
        """
        now=_utc(now or datetime.now(timezone.utc))
        old_start,new_start,new_end=(_utc(x) for x in (old_start,new_start,new_end))
        if new_start <= now or new_end <= new_start:
            raise ValueError("Invalid new appointment")
        new_day = new_start.astimezone(TZ).date().isoformat()
        if new_end.astimezone(TZ).date().isoformat() != new_day:
            raise ValueError("Appointment crosses Chile day")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=db.execute(
                    """SELECT f.hold_id,f.redeemed_at,h.starts_at,h.status
                       FROM psychology_fonasa_bookings f
                       JOIN psychology_booking_holds h ON h.id=f.hold_id
                       WHERE f.source_id=?""",(source_id,)
                ).fetchone()
                if not row or row["status"]!="CONFIRMED" or row["redeemed_at"]:
                    raise StorageConflict("Fonasa link already used or missing")
                if _read_utc(row["starts_at"]) != old_start:
                    raise StorageConflict("Fonasa original time mismatch")
                if old_start-now < timedelta(hours=24):
                    raise StorageConflict("Fonasa rebooking deadline passed")
                self._ensure_available(db,new_day,new_start,new_end,now,exclude_hold=row["hold_id"])
                db.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES (?)",(new_day,))
                updated=db.execute(
                    """UPDATE psychology_fonasa_bookings
                       SET redeemed_at=?,old_starts_at=?,new_starts_at=?
                       WHERE source_id=? AND redeemed_at IS NULL""",
                    (_iso(now),_iso(old_start),_iso(new_start),source_id)
                )
                if updated.rowcount != 1:
                    raise StorageConflict("Fonasa link already redeemed")
                db.execute(
                    """UPDATE psychology_booking_holds SET slot_date=?,starts_at=?,ends_at=?,
                              expires_at=?,updated_at=? WHERE id=? AND status='CONFIRMED'""",
                    (new_day,_iso(new_start),_iso(new_end),_iso(new_end),
                     _iso(now),row["hold_id"])
                )
                return "REBOOKED"
        except sqlite3.Error:
            raise StorageUnavailable("Fonasa reschedule storage failed") from None

    def calendar_pending(self, order_id, *, now=None):
        """Calendar writes intentionally disabled; authorized payment remains held."""
        moment = _utc(now or datetime.now(timezone.utc))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=db.execute(
                    "SELECT state FROM psychology_demo_payments WHERE order_id=?",
                    (order_id,)
                ).fetchone()
                if not row or row["state"] not in ("AUTHORIZED","CALENDAR_PENDING","CONFIRMED"):
                    raise StorageConflict("Not an authorized order")
                if row["state"]=="CONFIRMED":
                    return "CONFIRMED"
                db.execute(
                    "UPDATE psychology_demo_payments SET state='CALENDAR_PENDING',updated_at=? WHERE order_id=?",
                    (_iso(moment),order_id)
                )
                return "CALENDAR_PENDING"
        except sqlite3.Error:
            raise StorageUnavailable("Calendar pending state unavailable") from None

    def calendar_failed(self, order_id, *, now=None):
        """Keep uncertain paid appointments blocked for manual reconciliation."""
        moment = _utc(now or datetime.now(timezone.utc))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=db.execute(
                    "SELECT state,hold_id FROM psychology_demo_payments WHERE order_id=?",
                    (order_id,)
                ).fetchone()
                if not row:
                    raise StorageConflict("Unknown order")
                if row["state"]=="CONFIRMED":
                    return "CONFIRMED"
                if row["state"] not in ("AUTHORIZED","CALENDAR_PENDING","RECONCILIATION_REQUIRED"):
                    raise StorageConflict("Cannot reconcile unpaid order")
                db.execute(
                    "UPDATE psychology_demo_payments SET state='RECONCILIATION_REQUIRED',updated_at=? WHERE order_id=?",
                    (_iso(moment),order_id))
                db.execute(
                    """UPDATE psychology_booking_holds SET status='RECONCILIATION_REQUIRED',
                       expires_at=?,updated_at=?
                       WHERE id=? AND status IN ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED')""",
                    (_iso(moment+timedelta(days=1)),_iso(moment),row["hold_id"])
                )
                return "RECONCILIATION_REQUIRED"
        except sqlite3.Error:
            raise StorageUnavailable("Calendar reconciliation storage failed") from None

    def calendar_confirmed(self, order_id, event_id, *, now=None):
        """Finalize ONLY after independently verifying own Google event.

        This method does not verify the event itself and must never be called
        before matching Google's deterministic event ID, slot, and service.
        """
        moment=_utc(now or datetime.now(timezone.utc))
        if not isinstance(event_id,str) or not 2<=len(event_id)<=80:
            raise ValueError("Invalid event reference")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row=db.execute(
                    """SELECT p.state,p.calendar_event_id,p.hold_id,h.status AS hold_status,
                              h.expires_at
                       FROM psychology_demo_payments p
                       JOIN psychology_booking_holds h ON p.hold_id=h.id
                       WHERE p.order_id=?""",(order_id,)
                ).fetchone()
                if not row:
                    raise StorageConflict("Unknown payment")
                if row["state"]=="CONFIRMED":
                    if row["calendar_event_id"] != event_id or row["hold_status"] != "CONFIRMED":
                        raise StorageConflict("Confirmed event mismatch")
                    return "CONFIRMED"
                if (row["state"] not in ("AUTHORIZED","CALENDAR_PENDING")
                        or row["hold_status"] != "PAYMENT_PENDING"
                        or _read_utc(row["expires_at"]) <= moment):
                    raise StorageConflict("Cannot confirm stale or unpaid hold")
                db.execute(
                    """UPDATE psychology_demo_payments
                       SET state='CONFIRMED',calendar_event_id=?,updated_at=?
                       WHERE order_id=?""",(event_id,_iso(moment),order_id)
                )
                db.execute(
                    "UPDATE psychology_booking_holds SET status='CONFIRMED',updated_at=? WHERE id=?",
                    (_iso(moment),row["hold_id"])
                )
                for role in ("professional","patient"):
                    db.execute(
                        """INSERT OR IGNORE INTO psychology_demo_notifications
                           (order_id,recipient_role,status)
                           VALUES (?,?,'pending')""",
                        (order_id,role)
                    )
                return "CONFIRMED"
        except sqlite3.Error:
            raise StorageUnavailable("Calendar confirmation persistence failed") from None

    def encrypted_google_oauth(self, key="owner"):
        """Read ONLY Fernet-encrypted ciphertext, never decrypted credentials."""
        if key != "owner":
            raise ValueError("Invalid credential key")
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT encrypted_refresh_token FROM psychology_google_oauth WHERE key=?",
                    (key,),
                ).fetchone()
                return row["encrypted_refresh_token"] if row else None
        except sqlite3.Error:
            raise StorageUnavailable("Encrypted OAuth record unavailable") from None
