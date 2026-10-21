"""Isolated tests for next-day BCC distribution."""
import base64
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from ttlock_daily import AttemptLedger, encrypt_code
from ttlock_distribution import distribute

class FakeResponse:
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return b'{"id":"test-id"}'

class DistributionTests(unittest.TestCase):
    def test_bcc_and_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"db.sqlite3")
            with sqlite3.connect(path) as db:
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
                 "VALHALLA_EMAIL_FROM":"AVOJA <noreply@example.com>",
                 "VALHALLA_RESEND_API_KEY":"FAKE",
                 "TTLOCK_ENCRYPTION_KEY":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")}
            with patch.dict(os.environ,env):
                ledger=AttemptLedger(path)
                ledger.initialize()
                ledger.claim("2026-10-21")
                ledger.save_created("2026-10-21",100,encrypt_code("123456"))
                ledger.mark("2026-10-21","emailed",code_id=100)
                sent=[]
                def fake_transport(request,timeout):
                    sent.append(json.loads(request.data))
                    return FakeResponse()
                self.assertEqual(distribute(path,"2026-10-21",transport=fake_transport),"accepted")
                self.assertEqual(distribute(path,"2026-10-21",transport=fake_transport),"already_attempted")
                self.assertEqual(len(sent),1)
                self.assertEqual(sent[0]["bcc"],["one@example.com"])
                self.assertEqual(sent[0]["to"],["contacto@valhallaoffice.cl"])
                self.assertIn("#123456#",sent[0]["text"])

    def test_default_disabled(self):
        with patch.dict(os.environ,{"TTLOCK_DISTRIBUTION_ENABLED":"false"}):
            self.assertEqual(distribute("/not-used","2026-10-21"),"disabled")

if __name__=="__main__": unittest.main()
