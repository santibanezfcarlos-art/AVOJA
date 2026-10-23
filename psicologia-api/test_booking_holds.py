"""Pruebas unitarias del piloto: NO consultan Google ni la base de datos."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from booking_holds import (
    CATALOG, MAX_DAILY, HOLD_SECONDS, HoldProblem,
    bp, can_hold, enabled, overlaps, slot_window,
)

TZ = ZoneInfo("America/Santiago")
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=TZ)


class HoldPolicyTests(unittest.TestCase):
    def test_catalog_uses_current_service_duration_and_amount(self):
        self.assertEqual(CATALOG["psicoterapia"], {"minutes": 45, "amount_clp": 30000})
        self.assertEqual(CATALOG["empleabilidad"], {"minutes": 90, "amount_clp": 45000})
        self.assertEqual(CATALOG["competencias"], {"minutes": 45, "amount_clp": 30000})
        self.assertEqual((MAX_DAILY, HOLD_SECONDS), (3, 600))

    def test_slot_is_future_and_has_server_duration(self):
        d, begin, finish = slot_window("empleabilidad", "2026-10-26", "09:00", now=NOW)
        self.assertEqual(d.isoformat(), "2026-10-26")
        self.assertEqual(int((finish-begin).total_seconds()/60), 90)

    def test_time_must_be_within_working_hours(self):
        bad_cases = [
            ("2026-10-26", "08:45", "psicoterapia"),
            ("2026-10-26", "19:30", "empleabilidad"),
            ("2026-10-26", "10:10", "psicoterapia"),
            ("2026-10-26", "99:00", "psicoterapia"),
            ("2026-10-23", "09:00", "psicoterapia"),
            ("2027-01-01", "09:00", "psicoterapia"),
        ]
        for d, t, service in bad_cases:
            with self.subTest(d=d, t=t):
                with self.assertRaises(HoldProblem):
                    slot_window(service, d, t, now=NOW)

    def test_overlap_uses_half_open_ranges(self):
        a = datetime(2026,10,26,9,0,tzinfo=TZ)
        b = a + timedelta(minutes=45)
        self.assertFalse(overlaps(a,b,b,b+timedelta(minutes=45)))
        self.assertTrue(overlaps(a,b,b-timedelta(minutes=1),b+timedelta(minutes=45)))

    def test_busy_slot_cannot_be_held(self):
        a = datetime(2026,10,26,9,0,tzinfo=TZ)
        self.assertFalse(can_hold([(a,a+timedelta(minutes=45))],a+timedelta(minutes=15),a+timedelta(minutes=60)))

    def test_three_active_holds_limit(self):
        a = datetime(2026,10,26,9,0,tzinfo=TZ)
        rows = [(a+timedelta(hours=i), a+timedelta(hours=i, minutes=45)) for i in range(3)]
        self.assertFalse(can_hold(rows,a+timedelta(hours=3),a+timedelta(hours=3,minutes=45)))
        self.assertTrue(can_hold(rows[:2],a+timedelta(hours=3),a+timedelta(hours=3,minutes=45)))

    def test_pilot_disabled_without_config(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(enabled())
        with patch.dict(os.environ,{"PSICOLOGIA_HOLDS_ENABLED":"true"},clear=True):
            self.assertFalse(enabled())

    def test_synthetic_calendar_is_restricted_to_isolated_nonpayment_pilot(self):
        from booking_holds import _calendar_free
        with patch.dict(os.environ, {
            "PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true",
            "PSICOLOGIA_HOLDS_CALENDAR_POLICY": "synthetic",
            "PSICOLOGIA_CHECKOUT_MODE": "disabled",
            "PSICOLOGIA_WEBPAY_MODE": "disabled",
        }, clear=True):
            self.assertTrue(_calendar_free(NOW, NOW + timedelta(minutes=45)))
        with patch.dict(os.environ, {
            "PSICOLOGIA_HOLDS_ISOLATED_PILOT": "true",
            "PSICOLOGIA_HOLDS_CALENDAR_POLICY": "synthetic",
            "PSICOLOGIA_CHECKOUT_MODE": "production",
        }, clear=True):
            with self.assertRaises(HoldProblem):
                _calendar_free(NOW, NOW + timedelta(minutes=45))
        with patch.dict(os.environ, {
            "PSICOLOGIA_HOLDS_CALENDAR_POLICY": "synthetic",
        }, clear=True):
            with self.assertRaises(HoldProblem):
                _calendar_free(NOW, NOW + timedelta(minutes=45))

    def test_pilot_health_does_not_expose_keys_and_is_disabled(self):
        from pilot_app import app
        with patch.dict(os.environ, {}, clear=True):
            with app.test_client() as client:
                res = client.get("/health")
                self.assertEqual(res.status_code, 200)
                data = res.get_json()
                self.assertFalse(data["holds_enabled"])
                self.assertFalse(data["database_configured"])
                self.assertFalse(data["production_payments"])
                self.assertFalse(data["patient_bookings"])
                self.assertNotIn("database_url", data)

    def test_errors_have_safe_codes(self):
        with self.assertRaises(HoldProblem) as context:
            slot_window("not-a-service","2026-10-26","09:00",now=NOW)
        self.assertEqual(context.exception.status,400)


if __name__ == "__main__":
    unittest.main()
