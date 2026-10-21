"""Offline tests: single-recipient privacy, audit and idempotency."""
import base64
import os
import secrets
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ttlock_daily import AttemptLedger, encrypt_code
from ttlock_distribution import distribute


class FakeMailer:
    configured = True
    def __init__(self): self.sent=[]
    def send_message(self,email,subject,text,idempotency_key=None):
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
                self.assertEqual(distribute(db_path,"2026-10-21",mailer=mailer),"accepted")
                self.assertEqual(len(mailer.sent),2)
                self.assertEqual([r[0] for r in mailer.sent],
                                 ["contacto@valhallaoffice.cl","one@example.com"])
                for sent in mailer.sent:
                    self.assertIn("#123456#",sent[2])
                self.assertEqual(distribute(db_path,"2026-10-21",mailer=mailer),"already_attempted")
                self.assertEqual(len(mailer.sent),2)

    def test_disabled_by_default(self):
        with patch.dict(os.environ,{"TTLOCK_DISTRIBUTION_ENABLED":"false"}):
            self.assertEqual(distribute("/not-used","2026-10-21"),"disabled")


if __name__=="__main__":
    unittest.main()
