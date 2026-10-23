"""Pruebas de Google Calendar sin red, sin credenciales reales y sin eventos."""
import os
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from google_calendar_writer import (
    CalendarWriter, CalendarSettings, CalendarIntegrationError,
    CalendarHttpError, event_id_for_hold,
)

TZ = ZoneInfo("America/Santiago")
START = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
END = START + timedelta(minutes=45)
HOLD = str(uuid.UUID("6a1be88e-9745-4cfb-b391-1ae812345678"))


def client(transport, write_enabled=False):
    return CalendarWriter(
        settings=CalendarSettings("calendar-test@example.invalid", {"type": "service_account"}, write_enabled),
        transport=transport,
    )


def google_event():
    return {
        "id": event_id_for_hold(HOLD),
        "summary": "Atención reservada · Carlos Santibáñez",
        "start": {"dateTime": START.isoformat()},
        "end": {"dateTime": END.isoformat()},
        "extendedProperties": {"private": {"psychology_hold_id": HOLD,
                                           "psychology_service": "psicoterapia"}},
    }


class GoogleCalendarWriterTests(unittest.TestCase):
    def test_no_credentials_defaults_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            adapter = CalendarWriter(transport=lambda *a: self.fail("Must not call Google"))
            self.assertFalse(adapter.settings.configured)
            with self.assertRaises(CalendarIntegrationError):
                adapter.is_free(START, END)

    def test_consumer_cannot_write_without_explicit_opt_in(self):
        adapter = client(lambda *a: self.fail("Must not call Google"))
        with self.assertRaisesRegex(CalendarIntegrationError, "desactivada"):
            adapter.create_paid_appointment(
                hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True)

    def test_write_disabled_when_not_isolated_or_when_payments_enabled(self):
        adapter = client(lambda *a: self.fail("Must not call Google"), write_enabled=True)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CalendarIntegrationError):
                adapter.create_paid_appointment(
                    hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True)
        with patch.dict(os.environ, {
            "PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true",
            "PSICOLOGIA_WEBPAY_MODE": "integration",
        }, clear=True):
            with self.assertRaises(CalendarIntegrationError):
                adapter.create_paid_appointment(
                    hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True)

    def test_approved_payment_server_proof_required(self):
        adapter = client(lambda *a: self.fail("Must not call Google"), write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            with self.assertRaisesRegex(CalendarIntegrationError, "bancaria"):
                adapter.create_paid_appointment(
                    hold_id=HOLD, service="psicoterapia", start=START, end=END)

    def test_event_identifiers_are_stable_and_legal(self):
        self.assertEqual(event_id_for_hold(HOLD), "psc"+HOLD.replace("-", ""))
        self.assertRegex(event_id_for_hold(HOLD), r"^psc[a-f0-9]{32}$")
        with self.assertRaises(CalendarIntegrationError):
            event_id_for_hold("not-an-id")

    def test_create_only_emits_generic_title_and_private_metadata(self):
        captured = []
        def transport(method, path, params, body):
            captured.append((method, path, params, body))
            return google_event()
        adapter = client(transport, write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            created = adapter.create_paid_appointment(
                hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True)
        self.assertEqual(created, event_id_for_hold(HOLD))
        method, path, params, body = captured[0]
        self.assertEqual(method, "POST")
        self.assertNotIn("http", path)
        self.assertEqual(params, {"sendUpdates": "none"})
        self.assertEqual(body["visibility"], "private")
        self.assertEqual(body["transparency"], "opaque")
        self.assertEqual(body["summary"], "Atención reservada · Carlos Santibáñez")
        self.assertNotIn("attendees", body)
        self.assertNotIn("rut", str(body).lower())
        self.assertNotIn("diagnóstico", str(body).lower())

    def test_retry_after_conflict_is_idempotent_only_if_ours(self):
        def transport(method, path, params, body):
            if method == "POST":
                raise CalendarHttpError(409)
            return google_event()
        adapter = client(transport, write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            self.assertEqual(adapter.create_paid_appointment(
                hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True),
                event_id_for_hold(HOLD))

    def test_conflicting_foreign_event_is_rejected(self):
        def transport(method, path, params, body):
            if method == "POST":
                raise CalendarHttpError(409)
            item = google_event()
            item["extendedProperties"] = {"private": {"psychology_hold_id": str(uuid.uuid4())}}
            return item
        adapter = client(transport, write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            with self.assertRaises(CalendarIntegrationError):
                adapter.create_paid_appointment(
                    hold_id=HOLD, service="psicoterapia", start=START, end=END, payment_verified=True)

    def test_cancel_refuses_deletion_of_foreign_event(self):
        actions = []
        def transport(method, path, params, body):
            actions.append(method)
            return {"id": event_id_for_hold(HOLD), "extendedProperties": {"private": {}}}
        adapter = client(transport, write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            with self.assertRaises(CalendarIntegrationError):
                adapter.cancel_own_appointment(HOLD)
        self.assertEqual(actions, ["GET"])

    def test_cancel_own_event(self):
        actions=[]
        def transport(method, path, params, body):
            actions.append(method)
            return google_event() if method == "GET" else {}
        adapter=client(transport,write_enabled=True)
        with patch.dict(os.environ, {"PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true"}, clear=True):
            self.assertTrue(adapter.cancel_own_appointment(HOLD))
        self.assertEqual(actions, ["GET", "DELETE"])

    def test_busy_recurring_and_all_day_items_overlap(self):
        def transport(method,path,params,body):
            return {"items": [
                {"start":{"dateTime":START.isoformat()},"end":{"dateTime":END.isoformat()}},
                {"start":{"date":"2026-11-04"},"end":{"date":"2026-11-05"}},
                {"start":{"dateTime":START.isoformat()},"end":{"dateTime":END.isoformat()},
                 "transparency":"transparent"},
                {"status":"cancelled"},
            ]}
        adapter=client(transport)
        self.assertEqual(len(adapter.list_busy(START,END)),2)
        self.assertFalse(adapter.is_free(START,END))

    def test_missing_event_time_fails_closed(self):
        adapter=client(lambda *args: {"items":[{"status":"confirmed","start":{}}]})
        with self.assertRaises(CalendarIntegrationError):
            adapter.list_busy(START,END)

    def test_pagination_includes_second_page(self):
        seen=[]
        def transport(method,path,params,body):
            seen.append(params)
            if "pageToken" not in params:
                return {"items":[],"nextPageToken":"NEXT"}
            return {"items":[{"start":{"dateTime":START.isoformat()},
                              "end":{"dateTime":END.isoformat()}}]}
        self.assertEqual(len(client(transport).list_busy(START,END)),1)
        self.assertEqual(seen[1]["pageToken"],"NEXT")

    def test_invalid_credentials_json_is_safely_rejected(self):
        with patch.dict(os.environ, {
            "PSICOLOGIA_GOOGLE_CALENDAR_ID": "mycalendar",
            "PSICOLOGIA_GOOGLE_SERVICE_ACCOUNT_JSON":"{not-json",
        },clear=True):
            with self.assertRaises(CalendarIntegrationError):
                CalendarSettings.from_environment()


if __name__=="__main__":
    unittest.main()
