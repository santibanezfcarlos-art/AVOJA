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

    def test_webpay_authorizes_and_confirms_only_once(self):
        import uuid
        class FakeGateway:
            mode="integration"
            token="A"*60
            commits=0
            def create_for_existing_hold(self,*,buy_order,session_id):
                self.order=buy_order
                self.session=session_id
                return {"token":self.token,
                        "url":"https://webpay3gint.transbank.cl/webpayserver/initTransaction"}
            def commit(self,token):
                self.commits+=1
                assert token==self.token
                return {"status":"AUTHORIZED","response_code":0,"amount":30000,
                        "buy_order":self.order,"session_id":self.session}
        fake=FakeGateway()
        with patch("booking_api.WebpayGateway",return_value=fake):
            hold=self.hold("09:00")
            self.assertEqual(hold.status_code,201,hold.get_json())
            with self.app.test_client() as client:
                start=client.post("/api/bookings/checkout",json={"hold_id":hold.get_json()["hold_id"]})
                self.assertEqual(start.status_code,200,start.get_json())
                self.assertEqual(start.get_json()["token_ws"],fake.token)
                first=client.get("/api/webpay/return?token_ws="+fake.token)
                second=client.get("/api/webpay/return?token_ws="+fake.token)
                self.assertEqual(first.status_code,200)
                self.assertIn(b"hora confirmada",first.data)
                self.assertIn(b"hora confirmada",second.data)
            self.assertEqual(fake.commits,1)
            with psycopg.connect(self.dsn) as c:
                booked=c.execute("SELECT status FROM psychology_booking_holds").fetchone()[0]
                pay=c.execute("SELECT state,amount_clp FROM psychology_payment_attempts").fetchone()
            self.assertEqual(booked,"CONFIRMED")
            self.assertEqual(pay,("APPROVED",30000))

    def test_webpay_abort_does_not_commit(self):
        class FakeGateway:
            mode="integration"
            token="B"*60
            commits=0
            def create_for_existing_hold(self,*,buy_order,session_id):
                return {"token":self.token,
                        "url":"https://webpay3gint.transbank.cl/webpayserver/initTransaction"}
            def commit(self,token):
                self.commits+=1
                raise AssertionError("No se debe realizar commit con TBK_TOKEN")
        fake=FakeGateway()
        with patch("booking_api.WebpayGateway",return_value=fake):
            hold=self.hold("10:00")
            with self.app.test_client() as client:
                pay=client.post("/api/bookings/checkout",json={"hold_id":hold.get_json()["hold_id"]})
                self.assertEqual(pay.status_code,200)
                ret=client.post("/api/webpay/return",data={"TBK_TOKEN":fake.token,"token_ws":fake.token})
                self.assertEqual(ret.status_code,200)
            self.assertEqual(fake.commits,0)
            with psycopg.connect(self.dsn) as c:
                state=c.execute("SELECT state FROM psychology_payment_attempts").fetchone()[0]
            self.assertEqual(state,"CANCELLED")

    def test_no_payment_without_explicit_flag(self):
        with patch.dict(os.environ,{"PSICOLOGIA_BOOKINGS_ENABLED":"false"}):
            self.assertEqual(self.hold("10:00").status_code,503)


if __name__=="__main__":
    unittest.main()
