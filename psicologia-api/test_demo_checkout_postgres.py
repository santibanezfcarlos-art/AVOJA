"""Prueba del recorrido entero sin redes externas; Postgres real en CI."""
import os
from datetime import datetime,timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import psycopg
from pilot_app import app
import demo_checkout

DATABASE=os.environ.get("TEST_HOLDS_DATABASE_URL","")
SECRET="SC-E2E-SANDBOX-SECURE-TEST-CODE-2026"
RECIPIENT="santibanezf.carlos@gmail.com"
TZ=ZoneInfo("America/Santiago")


class FakeGateway:
    def __init__(self, *, approve=True):
        self.approve=approve
        self.order=None
        self.session=None
        self.service=None
        self.commit_count=0
        self.token="T"*64

    def create_for_existing_hold(self, *, order,session_id,service):
        self.order=order
        self.session=session_id
        self.service=service
        return {"token":self.token,"url":"https://webpay3gint.transbank.cl/webpayserver/initTransaction"}

    def commit(self,token):
        self.commit_count+=1
        if token!=self.token:
            raise AssertionError("wrong token")
        return {
            "status":"AUTHORIZED" if self.approve else "FAILED",
            "response_code":0 if self.approve else -1,
            "amount":demo_checkout.AMOUNTS[self.service],
            "buy_order":self.order,
            "session_id":self.session,
        }
    def status(self,token):
        return self.commit(token)


class FakeCalendarWriter:
    events=[]
    def __init__(self):
        self.settings=type("Settings",(),{"write_enabled":True})()
    def is_free(self,start,end):
        return True
    def own_event_exists(self,**kwargs):
        return False
    def create_paid_appointment(self,*,hold_id,service,start,end,payment_verified,demo_only):
        assert payment_verified and demo_only
        self.events.append(str(hold_id))
        return "psc"+hold_id.hex


@unittest.skipUnless(DATABASE, "Requiere PostgreSQL temporal de GitHub Actions")
class PilotCheckoutIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{
            "PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
            "PSICOLOGIA_HOLDS_ENABLED":"false",
            "PSICOLOGIA_PILOT_WEBPAY_TEST_ENABLED":"true",
            "PSICOLOGIA_HOLDS_DATABASE_URL":DATABASE,
            "PSICOLOGIA_HOLDS_CALENDAR_POLICY":"google_api",
            "PSICOLOGIA_API_PUBLIC_URL":"https://psicologia-retenciones-piloto.onrender.com",
            "PSICOLOGIA_PILOT_TEST_EMAIL":RECIPIENT,
            "PSICOLOGIA_PILOT_PRO_EMAIL":RECIPIENT,
            "PSICOLOGIA_DEMO_ACCESS_CODE":SECRET,
            "PSICOLOGIA_WEBPAY_MODE":"disabled",
            "PSICOLOGIA_CHECKOUT_MODE":"disabled",
            "PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED":"false",
        },clear=True)
        self.environment.start()
        self.calendar=patch("demo_checkout._calendar_free",return_value=True)
        self.calendar.start()
        self.writer=patch("demo_checkout.CalendarWriter",FakeCalendarWriter)
        self.writer.start()
        self.sent=patch("demo_checkout.mail_configured",return_value=False)
        self.sent.start()
        FakeCalendarWriter.events=[]
        with psycopg.connect(DATABASE) as conn:
            from pathlib import Path
            conn.execute(Path(__file__).with_name("schema_holds.sql").read_text("utf8"))
            conn.execute("""TRUNCATE TABLE psychology_demo_notifications,
                psychology_demo_payments,psychology_booking_holds,psychology_booking_days CASCADE""")
        self.day=(datetime.now(TZ)+timedelta(days=2)).date().isoformat()

    def tearDown(self):
        self.sent.stop()
        self.writer.stop()
        self.calendar.stop()
        self.environment.stop()

    def initiate(self,gateway):
        with patch("demo_checkout.gateway",return_value=gateway):
            with app.test_client() as client:
                resp=client.post("/demo/start",data={
                    "date":self.day,"time":"18:00","service":"psicoterapia",
                    "access_code":SECRET},headers={
                        "Origin":"https://psicologia-retenciones-piloto.onrender.com"})
                return resp

    def finish(self,gateway):
        with patch("demo_checkout.gateway",return_value=gateway):
            with app.test_client() as client:
                return client.post("/demo/return",data={"token_ws":gateway.token})

    def status(self):
        with psycopg.connect(DATABASE) as conn:
            return conn.execute("""SELECT p.state AS payment,h.status AS hold,
                       (SELECT count(*) FROM psychology_demo_notifications n
                        WHERE n.order_id=p.order_id) AS notices
                       FROM psychology_demo_payments p
                       JOIN psychology_booking_holds h ON h.id=p.hold_id""").fetchone()

    def test_authorized_creates_one_calendar_event_and_two_pending_notifications(self):
        gw=FakeGateway()
        self.assertEqual(self.initiate(gw).status_code,200)
        self.assertEqual(self.status(),("PAYMENT_PENDING","PAYMENT_PENDING",0))
        callback=self.finish(gw)
        self.assertEqual(callback.status_code,303)
        self.assertEqual(self.status(),("CONFIRMED","CONFIRMED",2))
        self.assertEqual(len(FakeCalendarWriter.events),1)
        self.assertEqual(gw.commit_count,1)
        with app.test_client() as c:
            page=c.get(callback.location)
            self.assertIn(b"Google Calendar",page.data)
            self.assertIn(b"no configurado",page.data)
        self.assertEqual(self.finish(gw).status_code,303)
        self.assertEqual(gw.commit_count,1)
        self.assertEqual(len(FakeCalendarWriter.events),1)

    def test_rejected_releases_hold_without_calendar_or_emails(self):
        gw=FakeGateway(approve=False)
        self.assertEqual(self.initiate(gw).status_code,200)
        self.assertEqual(self.finish(gw).status_code,303)
        self.assertEqual(self.status(),("REJECTED","CANCELLED",0))
        self.assertEqual(FakeCalendarWriter.events,[])

    def test_second_simultaneous_attempt_fails(self):
        first=FakeGateway()
        second=FakeGateway()
        self.assertEqual(self.initiate(first).status_code,200)
        self.assertEqual(self.initiate(second).status_code,409)
        with psycopg.connect(DATABASE) as conn:
            count=conn.execute("SELECT count(*) FROM psychology_demo_payments").fetchone()[0]
        self.assertEqual(count,1)

    def test_calendar_write_disabled_keeps_payment_verified_not_confirmed(self):
        class NotWritable(FakeCalendarWriter):
            def __init__(self):
                super().__init__()
                self.settings.write_enabled=False
        gw=FakeGateway()
        self.assertEqual(self.initiate(gw).status_code,200)
        with patch("demo_checkout.CalendarWriter",NotWritable):
            self.assertEqual(self.finish(gw).status_code,303)
        self.assertEqual(self.status(),("CALENDAR_PENDING","PAYMENT_PENDING",0))
        self.assertEqual(FakeCalendarWriter.events,[])


if __name__=="__main__":
    unittest.main()
