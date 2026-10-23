"""Prueba del puente público con autenticación HMAC y PostgreSQL de CI."""
import hashlib
import hmac
import json
import os
import time
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import psycopg
from pilot_app import app
from test_demo_checkout_postgres import FakeCalendarWriter

DB=os.environ.get("TEST_HOLDS_DATABASE_URL")
MAIL="santibanezf.carlos@gmail.com"
SECRET="very-long-test-integration-secret-2026-psc-0123456789"
ORDER="PSCD"+"A"*18
SESSION="D2P"+"20261104"+"1800"+"a"*28
TZ=ZoneInfo("America/Santiago")

def sign(data, secret=SECRET,stamp=None):
    payload=json.dumps(data,ensure_ascii=True,sort_keys=True,separators=(",",":")).encode()
    stamp=str(int(time.time()) if stamp is None else stamp)
    digest=hmac.new(secret.encode(),stamp.encode()+b"."+payload,hashlib.sha256).hexdigest()
    return payload,{"Content-Type":"application/json",
        "X-Psc-Timestamp":stamp,"X-Psc-Signature":digest}

class BridgeSignatureTests(unittest.TestCase):
    def test_requires_configuration_and_correct_hmac(self):
        body,headers=sign({"order":ORDER})
        with patch.dict(os.environ,{},clear=True):
            with app.test_client() as c:
                self.assertEqual(c.post("/internal/psicologia/demo/preflight",data=body,headers=headers).status_code,403)

    def test_rejects_wrong_signature_or_old_timestamp(self):
        env={"PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
             "PSICOLOGIA_HOLDS_CALENDAR_POLICY":"google_api",
             "PSICOLOGIA_DEMO_BRIDGE_KEY":SECRET,"PSICOLOGIA_HOLDS_DATABASE_URL":"x"}
        body,headers=sign({"order":ORDER})
        with patch.dict(os.environ,env,clear=True):
            with app.test_client() as c:
                self.assertEqual(c.post("/internal/psicologia/demo/preflight",data=body,
                                headers={**headers,"X-Psc-Signature":"0"*64}).status_code,403)
                old,oldheaders=sign({"order":ORDER},stamp=1234567890)
                self.assertEqual(c.post("/internal/psicologia/demo/preflight",data=old,headers=oldheaders).status_code,403)

    def test_preflight_fails_without_mail_ready(self):
        env={"PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
             "PSICOLOGIA_HOLDS_CALENDAR_POLICY":"google_api",
             "PSICOLOGIA_DEMO_BRIDGE_KEY":SECRET,"PSICOLOGIA_HOLDS_DATABASE_URL":"x"}
        body,headers=sign({"order":ORDER})
        with patch.dict(os.environ,env,clear=True):
            with app.test_client() as c:
                self.assertEqual(c.post("/internal/psicologia/demo/preflight",
                                       data=body,headers=headers).get_json()["state"],
                                 "NOTIFICATIONS_DISABLED")

@unittest.skipUnless(DB,"Requiere PostgreSQL temporal de GitHub Actions")
class BridgePostgresTests(unittest.TestCase):
    def setUp(self):
        env={
            "PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
            "PSICOLOGIA_HOLDS_CALENDAR_POLICY":"google_api",
            "PSICOLOGIA_HOLDS_DATABASE_URL":DB,
            "PSICOLOGIA_DEMO_BRIDGE_KEY":SECRET,
            "PSICOLOGIA_PILOT_TEST_EMAIL":MAIL,
            "PSICOLOGIA_PILOT_PRO_EMAIL":MAIL,
            "PSICOLOGIA_CALENDAR_WRITE_ENABLED":"true",
            "PSICOLOGIA_WEBPAY_MODE":"disabled",
            "PSICOLOGIA_CHECKOUT_MODE":"disabled",
        }
        self.env=patch.dict(os.environ,env,clear=True);self.env.start()
        self.ready=patch("public_demo_bridge.notification_ready",return_value=True);self.ready.start()
        self.calendar=patch("public_demo_bridge._calendar_free",return_value=True);self.calendar.start()
        self.writer=patch("demo_checkout.CalendarWriter",FakeCalendarWriter);self.writer.start()
        self.mail=patch("demo_checkout.mail_configured",return_value=False);self.mail.start()
        FakeCalendarWriter.events=[]
        with psycopg.connect(DB) as con:
            from pathlib import Path
            con.execute(Path(__file__).with_name("schema_holds.sql").read_text("utf-8"))
            con.execute("""TRUNCATE psychology_demo_notifications,psychology_demo_payments,
                   psychology_booking_holds,psychology_booking_days CASCADE""")
        self.day=(datetime.now(TZ)+timedelta(days=5)).date().isoformat()
        self.session="D2P"+self.day.replace("-","")+"1800"+("a"*28)

    def tearDown(self):
        self.mail.stop();self.writer.stop();self.calendar.stop()
        self.ready.stop();self.env.stop()

    def post(self,path,data):
        body,headers=sign(data)
        with app.test_client() as c:
            return c.post(path,data=body,headers=headers)

    def prepare(self):
        data={"order":ORDER,"session":self.session,"service":"psicoterapia",
              "date":self.day,"time":"18:00","amount":30000,"email":MAIL,"email_consent":True}
        return self.post("/internal/psicologia/demo/preflight",data)

    def finalize(self,outcome):
        data={"order":ORDER,"session":self.session,"amount":30000,"outcome":outcome}
        return self.post("/internal/psicologia/demo/complete",data)

    def test_success_confirmed_calendar_two_emails_pending_and_idempotent(self):
        self.assertEqual(self.prepare().get_json()["state"],"READY")
        res=self.finalize("approved")
        self.assertEqual(res.status_code,200)
        self.assertEqual(res.get_json()["state"],"CONFIRMED_EMAIL_PENDING")
        self.assertEqual(len(FakeCalendarWriter.events),1)
        self.assertEqual(self.finalize("approved").get_json()["state"],"CONFIRMED_EMAIL_PENDING")
        self.assertEqual(len(FakeCalendarWriter.events),1)
        with psycopg.connect(DB) as con:
            row=con.execute("SELECT state FROM psychology_demo_payments WHERE order_id=%s",(ORDER,)).fetchone()
            self.assertEqual(row[0],"CONFIRMED")
            self.assertEqual(con.execute("SELECT count(*) FROM psychology_demo_notifications").fetchone()[0],2)

    def test_rejected_cancels_hold_without_calendar(self):
        self.assertEqual(self.prepare().get_json()["state"],"READY")
        self.assertEqual(self.finalize("rejected").get_json()["state"],"REJECTED")
        self.assertEqual(FakeCalendarWriter.events,[])
        with psycopg.connect(DB) as con:
            self.assertEqual(con.execute("SELECT status FROM psychology_booking_holds").fetchone()[0],"CANCELLED")

    def test_rejects_duplicate_time_and_unapproved_email(self):
        self.assertEqual(self.prepare().get_json()["state"],"READY")
        self.assertEqual(self.post("/internal/psicologia/demo/preflight",{
            "order":"PSCD"+"B"*18,"session":self.session.replace("a"*28,"b"*28),
            "service":"psicoterapia","date":self.day,"time":"18:00",
            "amount":30000,"email":MAIL,"email_consent":True}).status_code,409)
        self.assertEqual(self.post("/internal/psicologia/demo/preflight",{
            "order":"PSCD"+"C"*18,"session":self.session.replace("a"*28,"c"*28),
            "service":"psicoterapia","date":self.day,"time":"19:00",
            "amount":30000,"email":"unverified@example.com","email_consent":True}).get_json()["state"],"TEST_EMAIL_ONLY")

    def test_customer_email_external_requires_opt_in_and_consent(self):
        order="PSCD"+"D"*18
        values={"order":order,"session":self.session.replace("a"*28,"d"*28),
                "service":"psicoterapia","date":self.day,"time":"19:00",
                "amount":30000,"email":"cliente.pruebas@outlook.com"}
        self.assertEqual(self.post("/internal/psicologia/demo/preflight",values).get_json()["state"],
                         "EMAIL_CONSENT_REQUIRED")
        values["email_consent"]=True
        self.assertEqual(self.post("/internal/psicologia/demo/preflight",values).get_json()["state"],
                         "TEST_EMAIL_ONLY")
        with patch.dict(os.environ,{"PSICOLOGIA_PILOT_ALLOW_EXTERNAL_TEST_EMAILS":"true"}):
            self.assertEqual(self.post("/internal/psicologia/demo/preflight",values).get_json()["state"],
                             "READY")
            with psycopg.connect(DB) as conn:
                stored=conn.execute("SELECT test_recipient FROM psychology_demo_payments WHERE order_id=%s",(order,)).fetchone()
                self.assertEqual(stored[0],"cliente.pruebas@outlook.com")

if __name__=="__main__":
    unittest.main()
