"""Pruebas de integración con PostgreSQL temporal; sin pacientes, Webpay ni Google.
Se ejecutan únicamente cuando existe TEST_HOLDS_DATABASE_URL.
"""
import os
import pathlib
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask
import psycopg
from booking_holds import bp

TZ = ZoneInfo("America/Santiago")
KEY = "key-test-only-no-authentication-prod-0123456789"
DB = os.environ.get("TEST_HOLDS_DATABASE_URL", "")


@unittest.skipUnless(DB, "Sólo en CI con PostgreSQL de prueba")
class PostgresHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn_string = DB
        cls.env = patch.dict(os.environ, {
            "PSICOLOGIA_HOLDS_ENABLED": "true",
            "PSICOLOGIA_HOLDS_DATABASE_URL": DB,
            "PSICOLOGIA_HOLDS_PILOT_KEY": KEY,
        })
        cls.env.start()
        with psycopg.connect(DB) as conn:
            conn.execute(pathlib.Path(__file__).with_name("schema_holds.sql").read_text())
        cls.calendar = patch("booking_holds._calendar_free", return_value=True)
        cls.calendar.start()
        cls.app = Flask("pilot_integration")
        cls.app.register_blueprint(bp)

    @classmethod
    def tearDownClass(cls):
        cls.calendar.stop()
        cls.env.stop()

    def setUp(self):
        with psycopg.connect(DB) as conn:
            conn.execute("TRUNCATE TABLE psychology_booking_holds,psychology_booking_days CASCADE")
        self.day = (datetime.now(TZ)+timedelta(days=2)).date().isoformat()

    def post(self, url, payload, *, key=KEY):
        with self.app.test_client() as client:
            result = client.post(url,json=payload,headers={"X-Pilot-Key":key})
            return result.status_code,result.get_json()

    def reserve(self, t="09:00", service="psicoterapia"):
        return self.post("/api/holds/pilot/create",
                         {"service":service,"date":self.day,"time":t})

    def test_db_hold_persists_and_overlapping_slot_blocked(self):
        status,first = self.reserve()
        self.assertEqual(status,201,first)
        self.assertEqual(first["expires_in_seconds"],600)
        with psycopg.connect(DB) as conn:
            count=conn.execute("SELECT count(*) FROM psychology_booking_holds WHERE status='HELD'").fetchone()[0]
        self.assertEqual(count,1)
        status,other=self.reserve("09:15")
        self.assertEqual(status,409,other)

    def test_only_three_non_overlapping_slots_allowed(self):
        for time in ("09:00","10:00","11:00"):
            self.assertEqual(self.reserve(time)[0],201)
        self.assertEqual(self.reserve("12:00")[0],409)

    def test_expiry_releases_slot(self):
        status,hold=self.reserve()
        self.assertEqual(status,201)
        with psycopg.connect(DB) as conn:
            conn.execute("UPDATE psychology_booking_holds SET expires_at=now()-interval '2 seconds' WHERE id=%s",(hold["hold_id"],))
        status,new=self.reserve()
        self.assertEqual(status,201,new)
        with psycopg.connect(DB) as conn:
            states=conn.execute("SELECT status,count(*) FROM psychology_booking_holds GROUP BY status").fetchall()
        self.assertEqual(dict(states),{"HELD":1,"EXPIRED":1})

    def test_cancel_requires_matching_secret(self):
        status,hold=self.reserve()
        self.assertEqual(status,201)
        payload={"hold_id":hold["hold_id"],"cancel_secret":"wrong"}
        self.assertEqual(self.post("/api/holds/pilot/cancel",payload)[0],403)
        payload["cancel_secret"]=hold["cancel_secret"]
        self.assertEqual(self.post("/api/holds/pilot/cancel",payload)[0],200)
        self.assertEqual(self.reserve()[0],201)

    def test_two_concurrent_calls_can_never_take_same_time(self):
        barrier=threading.Barrier(2)
        def race(_):
            barrier.wait(timeout=10)
            return self.reserve("09:00")[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(race, range(2)))
        self.assertEqual(sorted(statuses),[201,409],statuses)
        with psycopg.connect(DB) as conn:
            count=conn.execute("SELECT count(*) FROM psychology_booking_holds WHERE status='HELD'").fetchone()[0]
        self.assertEqual(count,1)

    def test_wrong_admin_key_rejected(self):
        status,_=self.post("/api/holds/pilot/create",
                           {"service":"psicoterapia","date":self.day,"time":"09:00"},
                           key="invalid")
        self.assertEqual(status,403)
        with psycopg.connect(DB) as conn:
            count=conn.execute("SELECT count(*) FROM psychology_booking_holds").fetchone()[0]
        self.assertEqual(count,0)


if __name__ == "__main__":
    unittest.main()
