"""Preproduction fault simulations without external calls or real payments."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pilot_app import app
from booking_holds import can_hold, slot_window, HoldProblem
from demo_notifications import template, NotificationError
from rebooking_tokens import issue, check
from google_calendar_writer import CalendarWriter, CalendarIntegrationError
from test_google_calendar_writer import client, google_event, HOLD, START, END
from test_rebooking import FakeDb

TZ = ZoneInfo("America/Santiago")
NEW = START + timedelta(days=1)


class PreproductionScenarios(unittest.TestCase):
    def test_simultaneous_hold_same_slot_rejected_by_policy(self):
        self.assertFalse(can_hold([(START, END)], START, END))
        self.assertFalse(can_hold([(START, END)], START+timedelta(minutes=15), END+timedelta(minutes=15)))

    def test_maximum_three_daily_sessions(self):
        windows=[(START+timedelta(hours=i), END+timedelta(hours=i)) for i in range(3)]
        self.assertFalse(can_hold(windows,START+timedelta(hours=4),END+timedelta(hours=4)))

    def test_slot_ending_after_closing_rejected(self):
        with self.assertRaises(HoldProblem):
            slot_window("empleabilidad", "2026-11-04", "19:00",
                        now=datetime(2026,10,24,tzinfo=TZ))

    def test_rebook_page_disabled_in_production_default(self):
        with patch.dict(os.environ,{},clear=True), app.test_client() as c:
            self.assertEqual(c.get("/reagendar").status_code,503)
            self.assertEqual(c.get("/api/reagendar/"+"a"*64+"/estado").status_code,503)
            self.assertEqual(c.post("/api/reagendar/"+"a"*64+"/confirmar",json={}).status_code,503)

    def test_no_link_in_email_by_default(self):
        _, html=template("patient","TEST","psicoterapia",START,END)
        self.assertNotIn("Reagendar mi atención</a>",html)

    def test_link_rejected_on_foreign_domain(self):
        with self.assertRaises(NotificationError):
            template("patient","TEST","psicoterapia",START,END,
                     rebooking_url="https://not-psc.example/reagendar?t=123")

    def test_professional_never_receives_patient_link(self):
        with self.assertRaises(NotificationError):
            template("professional","TEST","psicoterapia",START,END,
                     rebooking_url="https://pscsantibanez.cl/reagendar?t=123")

    def test_expired_token_even_if_database_confirmed(self):
        current=datetime(2026,10,25,10,tzinfo=TZ)
        db=FakeDb(current+timedelta(hours=20))
        with patch.dict(os.environ,{"PSICOLOGIA_REBOOKING_TOKEN_SECRET":"x"*64}):
            token=issue(db,"PSI-TEST")
        self.assertEqual(check(db,token,now=current)[1],"too_late")

    def test_calendar_patch_requires_explicit_write(self):
        writer=client(lambda *args: self.fail("Network must not be called"))
        with self.assertRaises(CalendarIntegrationError):
            writer.reschedule_own_appointment(hold_id=HOLD,service="psicoterapia",
                old_start=START,old_end=END,new_start=NEW,new_end=NEW+timedelta(minutes=45))

    def test_calendar_rejects_event_not_owned(self):
        event=google_event()
        event["extendedProperties"]["private"]["psychology_hold_id"]="other"
        writer=client(lambda method,*rest: event,write_enabled=True)
        env={"PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
             "PSICOLOGIA_WEBPAY_MODE":"disabled","PSICOLOGIA_CHECKOUT_MODE":"disabled"}
        with patch.dict(os.environ,env):
            with self.assertRaises(CalendarIntegrationError):
                writer.reschedule_own_appointment(hold_id=HOLD,service="psicoterapia",
                    old_start=START,old_end=END,new_start=NEW,new_end=NEW+timedelta(minutes=45))

    def test_calendar_patch_failure_does_not_report_success(self):
        def transport(method,*rest):
            if method=="GET":
                return google_event()
            raise CalendarIntegrationError("simulated Google failure")
        writer=client(transport,write_enabled=True)
        env={"PSICOLOGIA_HOLDS_ISOLATED_PILOT":"true",
             "PSICOLOGIA_WEBPAY_MODE":"disabled","PSICOLOGIA_CHECKOUT_MODE":"disabled"}
        with patch.dict(os.environ,env):
            with self.assertRaises(CalendarIntegrationError):
                writer.reschedule_own_appointment(hold_id=HOLD,service="psicoterapia",
                    old_start=START,old_end=END,new_start=NEW,new_end=NEW+timedelta(minutes=45))

if __name__=="__main__":
    unittest.main()
