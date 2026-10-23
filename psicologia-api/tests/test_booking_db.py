"""Integración PostgreSQL efímera SOLO en GitHub Actions; sin pagos reales."""
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
import psycopg
from booking_api import booking_bp


@unittest.skipUnless(os.environ.get("PSICOLOGIA_DATABASE_URL"),"Sin base de datos de pruebas")
class BookingDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.dsn=os.environ["PSICOLOGIA_DATABASE_URL"]
        with psycopg.connect(self.dsn) as c:
            c.execute("TRUNCATE psychology_payment_attempts, psychology_booking_holds, psychology_booking_days CASCADE")
        self.app=Flask("booking-db-test")
        self.app.register_blueprint(booking_bp)
        self.env=patch.dict(os.environ,{
            "PSICOLOGIA_DATABASE_URL":self.dsn,
            "PSICOLOGIA_BOOKINGS_ENABLED":"true",
            "PSICOLOGIA_WEBPAY_MODE":"integration",
            "PSICOLOGIA_API_PUBLIC_URL":"https://psicologia-carlos-calendar-api.onrender.com",
        })
        self.env.start()
        self.calendar=patch("booking_api._calendar_free",return_value=True)
        self.calendar.start()
        self.d=(datetime.now(ZoneInfo("America/Santiago"))+timedelta(days=5)).date().isoformat()

    def tearDown(self):
        self.calendar.stop()
        self.env.stop()

    def hold(self, start):
        with self.app.test_client() as client:
            return client.post("/api/bookings/hold",json={
                "date":self.d,"time":start,"name":"Prueba de reserva",
                "email":"prueba@example.com"
            })

    def test_same_slot_cannot_be_retained_twice(self):
        a=self.hold("10:00")
        b=self.hold("10:00")
        self.assertEqual(a.status_code,201,a.get_json())
        self.assertEqual(b.status_code,409,b.get_json())

    def test_three_daily_capacity(self):
        for h in ["09:00","11:00","13:00"]:
            self.assertEqual(self.hold(h).status_code,201)
        self.assertEqual(self.hold("15:00").status_code,409)

    def test_four_simultaneous_requests_max_three(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses=list(executor.map(self.hold,["09:00","11:00","13:00","15:00"]))
        self.assertEqual(sum(r.status_code==201 for r in responses),3,
                         [r.status_code for r in responses])
        self.assertEqual(sum(r.status_code==409 for r in responses),1)
        with psycopg.connect(self.dsn) as c:
            result=c.execute("SELECT count(*) FROM psychology_booking_holds").fetchone()[0]
        self.assertEqual(result,3)

    def test_overlapping_slot_rejected(self):
        self.assertEqual(self.hold("10:00").status_code,201)
        self.assertEqual(self.hold("10:45").status_code,409)
        self.assertEqual(self.hold("11:00").status_code,201)

    def test_no_payment_without_explicit_flag(self):
        with patch.dict(os.environ,{"PSICOLOGIA_BOOKINGS_ENABLED":"false"}):
            self.assertEqual(self.hold("10:00").status_code,503)


if __name__=="__main__":
    unittest.main()
