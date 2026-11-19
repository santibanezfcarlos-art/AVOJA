"""Offline tests: single-recipient privacy, audit and idempotency."""
import base64
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ttlock_daily import AttemptLedger, encrypt_code
from mailer import EmailDeliveryError
from ttlock_distribution import _initialize, _send_one, distribute


class FakeMailer:
    configured = True
    def __init__(self): self.sent=[]
    def send_message(self,email,subject,text,idempotency_key=None,html=None):
        self.sent.append((email,subject,text,idempotency_key))


class Tests(unittest.TestCase):
    def test_private_separate_messages_and_no_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path=str(Path(directory)/"demo.db")
            with sqlite3.connect(db_path) as db:
                db.executescript("""
                  CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT);
                  CREATE TABLE bookings(id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,start TEXT);
                  INSERT INTO users VALUES(1,'one@example.com'),(2,'two@example.com');
                  INSERT INTO bookings VALUES
                     (1,1,'confirmed','2026-10-22T13:00:00+00:00'),
                     (2,1,'confirmed','2026-10-22T14:00:00+00:00'),
                     (3,2,'cancelled','2026-10-22T15:00:00+00:00');
                """)
            env={"TTLOCK_DISTRIBUTION_ENABLED":"true",
                 "TTLOCK_EMAIL_TO":"owner@example.com",
                 "TTLOCK_DISTRIBUTION_TO":"contacto@valhallaoffice.cl",
                 "TTLOCK_ENCRYPTION_KEY":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")}
            with patch.dict(os.environ,env):
                ledger=AttemptLedger(db_path)
                ledger.initialize()
                ledger.claim("2026-10-21")
                ledger.save_created("2026-10-21",100,encrypt_code("123456"))
                ledger.mark("2026-10-21","emailed",code_id=100)
                mailer=FakeMailer()
                # Freeze the clock within the code's validity window.
                # The fixture date is historical, so using wall-clock time
                # would incorrectly make this delivery test fail as expired.
                from datetime import datetime
                from zoneinfo import ZoneInfo
                class FrozenDateTime(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        fixed = datetime(2026, 10, 21, 23, tzinfo=ZoneInfo("America/Santiago"))
                        return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)
                with patch("ttlock_distribution.datetime", FrozenDateTime):
                    self.assertEqual(distribute(db_path,"2026-10-21",mailer=mailer),"accepted")
                    self.assertEqual(len(mailer.sent),2)
                    self.assertEqual([r[0] for r in mailer.sent],
                                     ["contacto@valhallaoffice.cl","one@example.com"])
                    for sent in mailer.sent:
                        self.assertIn("#123456#",sent[2])
                    self.assertEqual(distribute(db_path,"2026-10-21",mailer=mailer),"already_attempted")
                    self.assertEqual(len(mailer.sent),2)

    def test_manual_saved_code_has_separate_idempotency_key(self):
        from ttlock_distribution import distribute_saved
        from datetime import datetime
        from zoneinfo import ZoneInfo
        with tempfile.TemporaryDirectory() as folder:
            db_path=str(Path(folder)/"test.db")
            with sqlite3.connect(db_path) as db:
                db.executescript("""
                    CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT);
                    CREATE TABLE bookings(id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,start TEXT);
                    INSERT INTO users VALUES(1,'one@example.com');
                    INSERT INTO bookings VALUES(1,1,'confirmed','2026-10-22T13:00:00+00:00');
                """)
            env={"TTLOCK_DISTRIBUTION_ENABLED":"true",
                 "TTLOCK_EMAIL_TO":"owner@example.com",
                 "TTLOCK_DISTRIBUTION_TO":"contacto@valhallaoffice.cl",
                 "TTLOCK_ENCRYPTION_KEY":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")}
            with patch.dict(os.environ,env):
                mailer=FakeMailer()
                start=datetime(2026,10,21,22,tzinfo=ZoneInfo("America/Santiago"))
                # This historical date is expired at test runtime. Freeze time in module.
                with patch("ttlock_distribution.datetime") as clock:
                    clock.now.return_value=datetime(2026,10,21,23,tzinfo=ZoneInfo("America/Santiago"))
                    clock.side_effect=lambda *a,**k: datetime(*a,**k)
                    self.assertEqual(distribute_saved(db_path,"manual-example",start,encrypt_code("456789"),mailer=mailer),"accepted")
                    self.assertEqual(distribute_saved(db_path,"manual-example",start,encrypt_code("456789"),mailer=mailer),"already_attempted")
                self.assertEqual(len(mailer.sent),2)
                self.assertEqual([s[0] for s in mailer.sent],["contacto@valhallaoffice.cl","one@example.com"])

    def test_rate_limit_retry_and_audit(self):
        cases = (
            ([EmailDeliveryError("rate_limited", 429, retry_after=3), "email-id"],
             ("accepted", "email-id", None, 2), [0.25, 3]),
            ([EmailDeliveryError("rate_limited", 429)] * 3,
             ("rate_limited", None, 429, 3), [0.25, 1, 2]),
            ([EmailDeliveryError("rate_limited", 429, retry_after=60)],
             ("rate_limited", None, 429, 1), [0.25]),
            ([EmailDeliveryError("network_error")],
             ("uncertain", None, None, 1), [0.25]),
            ([EmailDeliveryError("invalid_sender_or_payload", 422)],
             ("rejected", None, 422, 1), [0.25]),
        )
        for responses, expected, delays in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as folder:
                db_path = str(Path(folder) / "test.db")
                with sqlite3.connect(db_path) as db:
                    db.execute("""CREATE TABLE ttlock_individual_delivery (
                        issue_day TEXT, recipient TEXT, booking_day TEXT,
                        status TEXT, created_at TEXT, PRIMARY KEY(issue_day, recipient))""")
                _initialize(db_path)
                _initialize(db_path)
                mailer = Mock()
                mailer.send_message.side_effect = responses
                args = (db_path, "issue", "booking", "user@example.com", "subject", "body", mailer)
                with patch("ttlock_distribution.sleep") as wait:
                    self.assertEqual(_send_one(*args), expected[0])
                    self.assertEqual(_send_one(*args), "already_attempted")
                self.assertEqual([call.args[0] for call in wait.call_args_list], delays)
                keys = [call.kwargs["idempotency_key"] for call in mailer.send_message.call_args_list]
                self.assertEqual(keys, ["ttlock-individual-issue-user@example.com"] * expected[3])
                with sqlite3.connect(db_path) as db:
                    self.assertEqual(db.execute("""SELECT status, provider_message_id,
                        http_status, attempts FROM ttlock_individual_delivery""").fetchone(), expected)

    def test_disabled_by_default(self):
        with patch.dict(os.environ,{"TTLOCK_DISTRIBUTION_ENABLED":"false"}):
            self.assertEqual(distribute("/not-used","2026-10-21"),"disabled")


if __name__=="__main__":
    unittest.main()
