"""Offline tests for the future SQLite transactional PSC store.

Only fabricated payment order IDs and appointments. No API calls, no emails,
no Transbank calls, no changes to actual calendars, no real passwords.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from psc_sqlite_txn_store import PSCStore, StorageConflict

NOW = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc)
DAY = date(2026, 11, 12)


def prepare(path):
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
CREATE TABLE psychology_booking_days (slot_date TEXT PRIMARY KEY NOT NULL);
CREATE TABLE psychology_booking_holds (
 id TEXT PRIMARY KEY,
 cancel_secret_sha256 TEXT NOT NULL,
 slot_date TEXT NOT NULL REFERENCES psychology_booking_days(slot_date),
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
 service TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('HELD','EXPIRED','CANCELLED','PAYMENT_PENDING','RECONCILIATION_REQUIRED','CONFIRMED')),
 expires_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE psychology_demo_payments (
 order_id TEXT PRIMARY KEY,hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
 session_id TEXT NOT NULL UNIQUE,token_sha256 TEXT UNIQUE,service TEXT NOT NULL,
 amount_clp INTEGER NOT NULL,test_recipient TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('STARTING','PAYMENT_PENDING','COMMITTING','AUTHORIZED','REJECTED','CANCELLED','CALENDAR_PENDING','CONFIRMED','RECONCILIATION_REQUIRED')),
 calendar_event_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 verified_at TEXT,patient_name TEXT
);
CREATE TABLE psychology_demo_notifications (
 order_id TEXT NOT NULL REFERENCES psychology_demo_payments(order_id),
 recipient_role TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 attempted_at TEXT,sent_at TEXT, timezone_correction_sent_at TEXT,
 PRIMARY KEY(order_id,recipient_role)
);
CREATE TABLE psychology_google_oauth (
 key TEXT PRIMARY KEY,encrypted_refresh_token TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE psychology_fonasa_bookings (
 source_id TEXT PRIMARY KEY,hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
 patient_name TEXT NOT NULL, patient_email TEXT NOT NULL,token_sha256 TEXT UNIQUE NOT NULL,
 created_at TEXT NOT NULL,redeemed_at TEXT,old_starts_at TEXT,new_starts_at TEXT
);
""")
        db.execute("INSERT INTO psychology_google_oauth VALUES('owner','gAAAA-CIPHERTEXT-NOT-PLAINTEXT',?)",
                   (NOW.isoformat(),))


class SQLiteTxnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "isolated.sqlite3"
        prepare(self.path)
        self.store = PSCStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def reserve(self, start=None, now=NOW, service="psicoterapia"):
        start = start or START
        return self.store.reserve(service, DAY, start, start + timedelta(minutes=45), now=now)

    def test_hold_created_and_secret_not_plaintext_in_db(self):
        hold = self.reserve()
        self.assertEqual(hold["status"], "HELD")
        with sqlite3.connect(self.path) as db:
            record = db.execute("SELECT status,cancel_secret_sha256 FROM psychology_booking_holds WHERE id=?",
                                (hold["id"],)).fetchone()
        self.assertEqual(record[0],"HELD")
        self.assertEqual(record[1],hashlib.sha256(hold["cancel_secret"].encode()).hexdigest())
        self.assertNotEqual(record[1],hold["cancel_secret"])

    def test_same_slot_only_one_success_across_threads(self):
        def attempt(_):
            try:
                return self.reserve()["status"]
            except StorageConflict:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(executor.map(attempt, range(12)))
        self.assertEqual(results.count("HELD"),1)
        self.assertEqual(results.count("CONFLICT"),11)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM psychology_booking_holds").fetchone()[0], 1)

    def test_daily_capacity_three_even_if_times_do_not_overlap(self):
        for minutes in (0,60,120):
            self.reserve(START + timedelta(minutes=minutes))
        with self.assertRaises(StorageConflict):
            self.reserve(START + timedelta(minutes=180))

    def test_expired_hold_does_not_count_and_can_no_longer_cancel(self):
        hold=self.reserve(now=NOW)
        after=NOW+timedelta(minutes=11)
        second=self.reserve(now=after)
        self.assertNotEqual(second["id"],hold["id"])
        with self.assertRaises(StorageConflict):
            self.store.cancel(hold["id"],hold["cancel_secret"],now=after)

    def test_secret_cancellation_is_idempotent_and_never_cancels_payment(self):
        hold=self.reserve()
        with self.assertRaises(StorageConflict):
            self.store.cancel(hold["id"],"invalid",now=NOW)
        self.assertEqual(self.store.cancel(hold["id"],hold["cancel_secret"],now=NOW),"CANCELLED")
        self.assertEqual(self.store.cancel(hold["id"],hold["cancel_secret"],now=NOW),"CANCELLED")
        fresh=self.reserve()
        self.store.mark_payment_pending(fresh["id"],"order1","session1","psicoterapia",30000,"example@invalid.test",now=NOW)
        with self.assertRaises(StorageConflict):
            self.store.cancel(fresh["id"],fresh["cancel_secret"],now=NOW)

    def test_payment_pending_and_approved_once(self):
        hold=self.reserve()
        self.assertEqual(self.store.mark_payment_pending(
            hold["id"],"order1","session1","psicoterapia",30000,"example@invalid.test",
            now=NOW),"PAYMENT_PENDING")
        self.assertEqual(self.store.mark_payment_pending(
            hold["id"],"order1","session1","psicoterapia",30000,"example@invalid.test",
            now=NOW),"PAYMENT_PENDING")
        with self.assertRaises(StorageConflict):
            self.store.mark_payment_pending(
                hold["id"],"order1","different","psicoterapia",30000,"example@invalid.test",
                now=NOW)
        self.assertEqual(self.store.complete_payment("order1","session1",30000,"approved",now=NOW),"AUTHORIZED")
        self.assertEqual(self.store.complete_payment("order1","session1",30000,"approved",now=NOW),"AUTHORIZED")
        with self.assertRaises(StorageConflict):
            self.store.complete_payment("order1","session1",30000,"cancelled",now=NOW)

    def test_expired_payment_approval_needs_reconciliation(self):
        hold=self.reserve()
        self.store.mark_payment_pending(hold["id"],"order2","session2","psicoterapia",30000,
                                        "example@invalid.test",now=NOW)
        later=NOW+timedelta(minutes=21)
        self.assertEqual(self.store.complete_payment("order2","session2",30000,"approved",now=later),
                         "RECONCILIATION_REQUIRED")
        with sqlite3.connect(self.path) as db:
            states=db.execute("SELECT status FROM psychology_booking_holds WHERE id=?",(hold["id"],)).fetchone()
        self.assertEqual(states[0],"RECONCILIATION_REQUIRED")

    def test_rejection_releases_hold(self):
        hold=self.reserve()
        self.store.mark_payment_pending(hold["id"],"order3","session3","psicoterapia",30000,
                                        "example@invalid.test",now=NOW)
        self.assertEqual(self.store.complete_payment("order3","session3",30000,"rejected",now=NOW),"REJECTED")
        replacement=self.reserve()
        self.assertNotEqual(replacement["id"],hold["id"])

    def test_preserves_oauth_encrypted_ciphertext_without_decrypting(self):
        self.assertEqual(self.store.encrypted_google_oauth(),"gAAAA-CIPHERTEXT-NOT-PLAINTEXT")
        with self.assertRaises(ValueError):
            self.store.encrypted_google_oauth("any-other-key")

    def _authorized_order(self):
        hold=self.reserve()
        self.store.mark_payment_pending(
            hold["id"],"paid-order","paid-session","psicoterapia",30000,
            "patient@invalid.test",now=NOW)
        self.store.complete_payment("paid-order","paid-session",30000,"approved",now=NOW)
        return hold

    def test_calendar_confirmation_is_atomic_and_idempotent(self):
        hold=self._authorized_order()
        self.assertEqual(self.store.calendar_confirmed("paid-order","calendar-verified-1",now=NOW),"CONFIRMED")
        self.assertEqual(self.store.calendar_confirmed("paid-order","calendar-verified-1",now=NOW),"CONFIRMED")
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT status FROM psychology_booking_holds WHERE id=?", (hold["id"],)).fetchone()
            notify=db.execute("SELECT recipient_role,status FROM psychology_demo_notifications ORDER BY recipient_role").fetchall()
        self.assertEqual(row[0],"CONFIRMED")
        self.assertEqual(notify,[("patient","pending"),("professional","pending")])
        with self.assertRaises(StorageConflict):
            self.store.calendar_confirmed("paid-order","another-event",now=NOW)

    def test_calendar_failure_remains_blocked_pending_reconciliation(self):
        self._authorized_order()
        self.assertEqual(self.store.calendar_failed("paid-order",now=NOW),"RECONCILIATION_REQUIRED")
        with self.assertRaises(StorageConflict):
            self.reserve()
        with self.assertRaises(StorageConflict):
            self.store.calendar_confirmed("paid-order","event",now=NOW)

    def test_calendar_pending_rejects_unpaid_orders(self):
        hold=self.reserve()
        with self.assertRaises(StorageConflict):
            self.store.calendar_pending("not-a-real-order",now=NOW)
        self.store.mark_payment_pending(
            hold["id"],"pending","session-p","psicoterapia",30000,
            "patient@invalid.test",now=NOW)
        with self.assertRaises(StorageConflict):
            self.store.calendar_pending("pending",now=NOW)
        self.store.complete_payment("pending","session-p",30000,"approved",now=NOW)
        self.assertEqual(self.store.calendar_pending("pending",now=NOW),"CALENDAR_PENDING")

    def test_stale_payment_cannot_get_calendar_confirmation(self):
        self._authorized_order()
        with self.assertRaises(StorageConflict):
            self.store.calendar_confirmed("paid-order","event",now=NOW+timedelta(minutes=21))
        with sqlite3.connect(self.path) as db:
            count=db.execute("SELECT COUNT(*) FROM psychology_demo_notifications").fetchone()[0]
        self.assertEqual(count,0)

    def register_fonasa(self, ident=None, start=None, token=None):
        identity = ident or str(uuid.uuid4())
        first = start or START
        hashed = hashlib.sha256((token or "fonasa-test-token").encode()).hexdigest()
        result=self.store.register_fonasa("voucher-fake",identity,
               "Ficticio Paciente","patient@invalid.test",hashed,
               first,first+timedelta(minutes=45),now=NOW)
        return identity,result

    def test_fonasa_register_is_idempotent_and_keeps_deterministic_hold(self):
        identity,result=self.register_fonasa()
        self.assertEqual(result,"CONFIRMED")
        _,again=self.register_fonasa(ident=identity)
        self.assertEqual(again,"CONFIRMED")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM psychology_fonasa_bookings").fetchone()[0],1)
            hold=db.execute("SELECT id,status FROM psychology_booking_holds").fetchone()
        self.assertEqual(hold,(identity,"CONFIRMED"))

    def test_fonasa_cannot_overwrite_another_existing_hold(self):
        hold=self.reserve()
        with self.assertRaises(StorageConflict):
            self.register_fonasa(ident=hold["id"])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM psychology_fonasa_bookings").fetchone()[0],0)

    def test_fonasa_token_is_one_time_and_old_calendar_id_persists(self):
        ident,_=self.register_fonasa()
        token="fonasa-test-token"
        self.assertEqual(self.store.fonasa_by_token(token,now=NOW)["state"],"ELIGIBLE")
        moved=START+timedelta(days=1)
        self.assertEqual(self.store.finalize_fonasa_reschedule(
            "voucher-fake",START,moved,moved+timedelta(minutes=45),now=NOW),"REBOOKED")
        self.assertEqual(self.store.fonasa_by_token(token,now=NOW)["state"],"USED")
        with sqlite3.connect(self.path) as db:
            hold=db.execute("SELECT id,slot_date,starts_at,status FROM psychology_booking_holds").fetchone()
        self.assertEqual(hold[0],ident)
        self.assertEqual(hold[1],"2026-11-13")
        self.assertEqual(hold[3],"CONFIRMED")
        with self.assertRaises(StorageConflict):
            self.store.finalize_fonasa_reschedule(
                "voucher-fake",START,moved+timedelta(hours=1),moved+timedelta(hours=2),now=NOW)

    def test_fonasa_rebooking_destination_overlap_rolls_back_all_updates(self):
        self.register_fonasa()
        future=START+timedelta(hours=1)
        self.reserve(start=future)
        with self.assertRaises(StorageConflict):
            self.store.finalize_fonasa_reschedule(
                "voucher-fake",START,future,future+timedelta(minutes=45),now=NOW)
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT redeemed_at FROM psychology_fonasa_bookings").fetchone()
        self.assertIsNone(row[0])
        self.assertEqual(self.store.fonasa_by_token("fonasa-test-token",now=NOW)["state"],"ELIGIBLE")

    def test_fonasa_register_prevents_slot_conflict(self):
        self.reserve()
        with self.assertRaises(StorageConflict):
            self.register_fonasa()

    def test_fonasa_token_deadline(self):
        self.register_fonasa()
        late=START-timedelta(hours=23)
        self.assertEqual(self.store.fonasa_by_token("fonasa-test-token",now=late)["state"],"TOO_LATE")

    def test_store_missing_does_not_create_a_database(self):
        location=Path(self.tmp.name)/"missing.sqlite3"
        with self.assertRaises(Exception):
            PSCStore(location)
        self.assertFalse(location.exists())

    def test_fonasa_and_calendar_not_triggered_by_storage(self):
        self.reserve()
        with sqlite3.connect(self.path) as db:
            count=db.execute("SELECT COUNT(*) FROM psychology_demo_payments").fetchone()[0]
        self.assertEqual(count,0)

if __name__=="__main__":
    unittest.main()
