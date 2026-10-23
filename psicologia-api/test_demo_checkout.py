"""Pruebas de ensayo integrado: no llaman Transbank, Resend ni Google."""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from demo_notifications import (
    allowed_email, configured as email_configured, ics_text, template, send, NotificationError,
)
from demo_checkout import pilot_enabled, _auto_submit
from pilot_app import app

MAIL = "santibanezf.carlos@gmail.com"
NOW = datetime(2026,11,4,13,0,tzinfo=ZoneInfo("America/Santiago"))
DEMO_ENV = {
    "PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true",
    "PSICOLOGIA_PILOT_TEST_EMAIL": MAIL,
    "PSICOLOGIA_PILOT_PRO_EMAIL": MAIL,
    "PSICOLOGIA_PILOT_WEBPAY_TEST_ENABLED": "true",
    "PSICOLOGIA_DEMO_ACCESS_CODE": "x"*32,
    "PSICOLOGIA_API_PUBLIC_URL": "https://psicologia-retenciones-piloto.onrender.com",
    "PSICOLOGIA_HOLDS_DATABASE_URL": "postgresql://test.invalid/postgres",
    "PSICOLOGIA_HOLDS_CALENDAR_POLICY": "google_api",
    "PSICOLOGIA_WEBPAY_MODE": "disabled",
    "PSICOLOGIA_CHECKOUT_MODE": "disabled",
}


class CheckoutDemoTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(pilot_enabled())
            with app.test_client() as client:
                res=client.get("/demo")
                self.assertEqual(res.status_code,503)

    def test_environment_requires_trial_site_and_no_production(self):
        with patch.dict(os.environ, DEMO_ENV,clear=True):
            self.assertTrue(pilot_enabled())
        with patch.dict(os.environ,{**DEMO_ENV,"PSICOLOGIA_CHECKOUT_MODE":"production"},clear=True):
            self.assertFalse(pilot_enabled())
        with patch.dict(os.environ,{**DEMO_ENV,"PSICOLOGIA_WEBPAY_MODE":"integration"},clear=True):
            self.assertFalse(pilot_enabled())
        with patch.dict(os.environ,{**DEMO_ENV,"PSICOLOGIA_API_PUBLIC_URL":"https://pscsantibanez.cl"},clear=True):
            self.assertFalse(pilot_enabled())

    def test_guest_cannot_start_checkout_without_secret(self):
        with patch.dict(os.environ,DEMO_ENV,clear=True):
            with app.test_client() as client:
                resp=client.post("/demo/start",data={
                    "service":"psicoterapia","date":"2026-11-04","time":"13:00",
                    "access_code":"invalid"},headers={"Origin":"https://psicologia-retenciones-piloto.onrender.com"})
            self.assertEqual(resp.status_code,403)

    def test_cross_origin_checkout_is_blocked(self):
        with patch.dict(os.environ,DEMO_ENV,clear=True):
            with app.test_client() as client:
                resp=client.post("/demo/start",data={
                    "service":"psicoterapia","date":"2026-11-04","time":"13:00",
                    "access_code":"x"*32},headers={"Origin":"https://somewhere.else"})
            self.assertEqual(resp.status_code,403)

    def test_google_and_mail_always_use_allowlisted_test_address(self):
        with patch.dict(os.environ,DEMO_ENV,clear=True):
            self.assertEqual(allowed_email(MAIL),MAIL)
            self.assertEqual(allowed_email("santibanezf.carlos+paciente1@gmail.com"),
                             "santibanezf.carlos+paciente1@gmail.com")
            self.assertIsNone(allowed_email("another@example.com"))
            self.assertIsNone(allowed_email("santibanezf.carlos+bad!@gmail.com"))
            self.assertIsNone(allowed_email("santibanezf.carlos+paciente1@outlook.com"))
            self.assertFalse(email_configured())

    def test_preview_does_not_contain_card_or_personal_details(self):
        subject,body=template("patient","PSI1234567890","psicoterapia",NOW,NOW+timedelta(minutes=45))
        self.assertIn("PRUEBA",subject)
        self.assertIn("SIN COBRO REAL",body)
        self.assertNotIn("RUT",body)

    def test_emails_use_local_chile_hour_even_when_postgres_returns_utc(self):
        # Octubre en Chile continental usa UTC-03.
        start=datetime(2026,10,24,15,0,tzinfo=timezone.utc)
        subject,body=template("patient","PSI1234567890","psicoterapia",
                              start,start+timedelta(minutes=45))
        self.assertIn("24/10/2026 · 12:00",subject)
        self.assertIn("12:00–12:45",body)
        self.assertNotIn("15:00",body)
        corrected_subject,corrected_body=template("patient","PSI1234567890",
                         "psicoterapia",start,start+timedelta(minutes=45),correction=True)
        self.assertIn("CORRECCIÓN DE HORARIO",corrected_subject)
        self.assertIn("en UTC",corrected_body)

    def test_calendar_invite_is_nonclinical_and_utc(self):
        val=ics_text(uuid.uuid4(),NOW,NOW+timedelta(minutes=45))
        self.assertIn("BEGIN:VEVENT",val)
        self.assertIn("DTSTART:20261104T160000Z",val)
        self.assertIn("SUMMARY:PRUEBA -",val)
        self.assertIn("STATUS:TENTATIVE",val)

    def test_two_emails_only_to_owner_in_trial(self):
        captured=[]
        def fake_transport(payload):
            captured.append(payload)
            return {"id":"email_demo_unique"}
        with patch.dict(os.environ,{**DEMO_ENV,
             "PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED":"true",
             "PSICOLOGIA_PILOT_RESEND_API_KEY":"test-only-key-not-real-"+("z"*30)},clear=True):
            for role in ("professional","patient"):
                send(role,"PSI0123456789",uuid.uuid4(),MAIL,"competencias",NOW,NOW+timedelta(minutes=45),
                     transport=fake_transport)
            self.assertEqual(len(captured),2)
            self.assertEqual([payload["to"] for payload in captured],[[MAIL],[MAIL]])
            self.assertTrue(all("PRUEBA" in p["subject"] for p in captured))
            self.assertTrue(all(p["attachments"][0]["filename"].endswith(".ics") for p in captured))
            with self.assertRaises(NotificationError):
                send("patient","PSI0123456789",uuid.uuid4(),"random@example.com",
                     "competencias",NOW,NOW+timedelta(minutes=45),transport=fake_transport)

    def test_patient_alias_email_goes_to_same_google_mailbox(self):
        captured=[]
        alias="santibanezf.carlos+paciente1@gmail.com"
        with patch.dict(os.environ,{**DEMO_ENV,
             "PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED":"true",
             "PSICOLOGIA_PILOT_RESEND_API_KEY":"test-only-key-not-real-"+("z"*30)},clear=True):
            send("patient","PSI0123456789",uuid.uuid4(),alias,"psicoterapia",
                 NOW,NOW+timedelta(minutes=45),
                 transport=lambda message: captured.append(message) or {"id":"test_email"})
        self.assertEqual(captured[0]["to"],[alias])
        self.assertIn("Confirmación",captured[0]["subject"])

    def test_external_email_only_with_opt_in_and_professional_fixed(self):
        receiver="cliente.pruebas@outlook.com"
        captured=[]
        with patch.dict(os.environ,{**DEMO_ENV,
             "PSICOLOGIA_PILOT_ALLOW_EXTERNAL_TEST_EMAILS":"true",
             "PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED":"true",
             "PSICOLOGIA_PILOT_RESEND_API_KEY":"test-only-key-not-real-"+("z"*30)},clear=True):
            self.assertEqual(allowed_email(receiver),receiver)
            self.assertIsNone(allowed_email("malformed_address"))
            self.assertIsNone(allowed_email("someone@localhost"))
            self.assertIsNone(allowed_email("bad..name@domain.com"))
            sent=send("patient","PSI0123456789",uuid.uuid4(),receiver,"psicoterapia",
                      NOW,NOW+timedelta(minutes=45),
                      transport=lambda payload:captured.append(payload) or {"id":"email_external_demo"})
            self.assertEqual(sent,"email_external_demo")
            self.assertEqual(captured[0]["to"],[receiver])
            with self.assertRaises(NotificationError):
                send("professional","PSI0123456789",uuid.uuid4(),receiver,"psicoterapia",
                     NOW,NOW+timedelta(minutes=45),
                     transport=lambda payload:{"id":"oops"})
        with patch.dict(os.environ,DEMO_ENV,clear=True):
            self.assertIsNone(allowed_email(receiver))

    def test_form_does_not_accept_real_patient_recipient(self):
        with patch.dict(os.environ,DEMO_ENV,clear=True):
            with app.test_client() as client:
                page=client.get("/demo")
            self.assertEqual(page.status_code,200)
            self.assertIn(b"prueba",page.data.lower())
            self.assertNotIn(b'name="email"',page.data)

    def test_post_to_transbank_is_manual_and_requires_official_url(self):
        resp=_auto_submit("a"*64,"https://webpay3gint.transbank.cl/webpayserver/initTransaction")
        self.assertEqual(resp.status_code,200)
        self.assertIn(b'Continuar a Webpay',resp.data)
        self.assertIn("https://webpay3gint.transbank.cl",resp.headers["Content-Security-Policy"])

    def test_no_card_numbers_are_stored_in_tables(self):
        from pathlib import Path
        sql=Path(__file__).with_name("schema_holds.sql").read_text("utf8")
        self.assertIn("psychology_demo_payments",sql)
        self.assertIn("psychology_demo_notifications",sql)
        self.assertNotIn("card_number",sql)
        self.assertNotIn("rut varchar",sql)


if __name__=="__main__":
    unittest.main()
