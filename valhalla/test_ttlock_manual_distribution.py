"""No-network verification of manual real-code distribution using stored ciphertext."""
import base64
import os
import secrets
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ttlock_daily import encrypt_code
from ttlock_distribution import distribute_manual_test

class FakeMailer:
    configured = True
    def __init__(self): self.sent = []
    def send_message(self,recipient,subject,body,idempotency_key=None):
        self.sent.append((recipient,subject,body,idempotency_key))

class ManualDistributionTests(unittest.TestCase):
    def test_manual_sends_once_per_professional_and_main(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder)/"db.sqlite3")
            with sqlite3.connect(db) as con:
                con.executescript("""
                    CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT);
                    CREATE TABLE bookings(id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,start TEXT);
                    CREATE TABLE ttlock_manual_tests(test_id TEXT PRIMARY KEY,status TEXT,created_at TEXT,encrypted_code TEXT);
                    INSERT INTO users VALUES (1,'pro@example.com'),(2,'pro@example.com'),(3,'cancel@example.com');
                    INSERT INTO bookings VALUES
                      (1,1,'confirmed','2026-10-22T13:00:00+00:00'),
                      (2,2,'confirmed','2026-10-22T14:00:00+00:00'),
                      (3,3,'cancelled','2026-10-22T15:00:00+00:00');
                """)
            env={"TTLOCK_DISTRIBUTION_ENABLED":"true",
                 "TTLOCK_EMAIL_TO":"owner@example.com",
                 "TTLOCK_DISTRIBUTION_TO":"contacto@valhallaoffice.cl",
                 "TTLOCK_ENCRYPTION_KEY":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")}
            with patch.dict(os.environ,env):
                with sqlite3.connect(db) as con:
                    con.execute("INSERT INTO ttlock_manual_tests VALUES (?,?,?,?)",
                        ("test-123","emailed","2026-10-22T01:00:00+00:00",encrypt_code("123456")))
                mailer = FakeMailer()
                self.assertEqual(distribute_manual_test(db,"test-123",mailer=mailer),"accepted")
                self.assertEqual([row[0] for row in mailer.sent],
                    ["contacto@valhallaoffice.cl","pro@example.com"])
                self.assertTrue(all("#123456#" in row[2] for row in mailer.sent))
                self.assertEqual(distribute_manual_test(db,"test-123",mailer=mailer),"already_attempted")
                self.assertEqual(len(mailer.sent),2)

if __name__=="__main__":
    unittest.main()
