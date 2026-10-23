"""Tests puros de agenda; no usan base de datos, bancos ni calendario reales."""
import sys
import unittest
from datetime import datetime, timedelta, time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from booking_api import _slot, _slot_taken, BookingProblem, enabled


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, *args):
        return self

    def fetchall(self):
        return self.rows


class BookingTests(unittest.TestCase):
    def future(self):
        return (datetime.now(ZoneInfo("America/Santiago")) + timedelta(days=2)).date().isoformat()

    def test_allowed_hours_and_duration(self):
        self.assertEqual(_slot(self.future(), "09:00")[1], time(9, 0))
        self.assertEqual(_slot(self.future(), "19:00")[1], time(19, 0))
        self.assertEqual(_slot(self.future(), "18:45")[1], time(18, 45))
        for hour in ["08:45", "19:15", "20:00", "09:13", "22:00"]:
            with self.subTest(hour=hour), self.assertRaises(BookingProblem):
                _slot(self.future(), hour)

    def test_third_hold_exhausts_day(self):
        t0 = uuid4()
        rows = [
            {"id": uuid4(), "starts_at": time(9,0), "ends_at": time(10,0)},
            {"id": uuid4(), "starts_at": time(11,0), "ends_at": time(12,0)},
            {"id": t0, "starts_at": time(16,0), "ends_at": time(17,0)},
        ]
        c=FakeConnection(rows)
        self.assertTrue(_slot_taken(c, self.future(), time(19,0)))
        self.assertFalse(_slot_taken(c, self.future(), time(19,0),exclude_id=t0))

    def test_overlapping_slots_not_allowed(self):
        c=FakeConnection([{"id":uuid4(),"starts_at":time(10,0),"ends_at":time(11,0)}])
        self.assertTrue(_slot_taken(c,self.future(),time(10,45)))
        self.assertTrue(_slot_taken(c,self.future(),time(9,15)))
        self.assertFalse(_slot_taken(c,self.future(),time(11,0)))

    def test_payment_feature_disabled_by_default(self):
        from unittest.mock import patch
        with patch.dict("os.environ",{"PSICOLOGIA_BOOKINGS_ENABLED":"false"}):
            self.assertFalse(enabled())


if __name__=="__main__":
    unittest.main()
