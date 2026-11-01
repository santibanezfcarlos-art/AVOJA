"""Offline SQLite occupancy tests using invented appointments, no patient data."""
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import tempfile
import unittest

from psc_busy_reader import busy_intervals, serialize
from psc_import_snapshot_sqlite import import_data
from test_psc_snapshot_conversion import fixture

NOW = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
DAY = date(2026, 11, 12)


class BusyReaderTests(unittest.TestCase):
    def prepare(self, folder, extra=None):
        data = fixture()
        data["tables"]["psychology_booking_holds"].extend(extra or [])
        data["counts"] = {t: len(rows) for t, rows in data["tables"].items()}
        db = Path(folder) / "staging.sqlite3"
        import_data(data, db)
        return db

    def duplicate(self, id_, state, *, hour, expiry="2026-11-12T18:45:00+00:00"):
        r = dict(fixture()["tables"]["psychology_booking_holds"][0])
        r.update(id=id_, status=state, starts_at=f"2026-11-12T{hour}:00+00:00",
                 ends_at=f"2026-11-12T{int(hour[:2]):02d}:{int(hour[3:])+30:02d}:00+00:00"
                 if int(hour[3:]) <= 29 else f"2026-11-12T{int(hour[:2])+1:02d}:00:00+00:00",
                 expires_at=expiry)
        return r

    def test_returns_confirmed_without_editing_staging(self):
        with tempfile.TemporaryDirectory() as folder:
            db = self.prepare(folder)
            before = hashlib.sha256(db.read_bytes()).hexdigest()
            rows = busy_intervals(db, DAY, DAY, now=NOW)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0].isoformat(), "2026-11-12T18:00:00+00:00")
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before)

    def test_expired_holds_do_not_block(self):
        with tempfile.TemporaryDirectory() as folder:
            old = self.duplicate(
                "33333333-3333-4333-8333-333333333333",
                "HELD", hour="19:00", expiry="2026-10-01T00:00:00+00:00"
            )
            db = self.prepare(folder, [old])
            self.assertEqual(len(busy_intervals(db, DAY, DAY, now=NOW)), 1)

    def test_three_active_slots_block_whole_chile_day(self):
        with tempfile.TemporaryDirectory() as folder:
            second = self.duplicate("33333333-3333-4333-8333-333333333333",
                                    "PAYMENT_PENDING", hour="19:00")
            third = self.duplicate("44444444-4444-4444-8444-444444444444",
                                   "RECONCILIATION_REQUIRED", hour="20:00")
            db = self.prepare(folder, [second, third])
            rows = busy_intervals(db, DAY, DAY, now=NOW)
            self.assertEqual(len(rows), 4)
            start, end = rows[-1]
            self.assertEqual(start.date().isoformat(), DAY.isoformat())
            self.assertEqual(end.date().isoformat(), "2026-11-13")
            self.assertIn("-03:00", start.isoformat())
            self.assertEqual(len(serialize(rows)), len(rows))

    def test_rejects_out_of_range_and_naive_time(self):
        with tempfile.TemporaryDirectory() as folder:
            db = self.prepare(folder)
            with self.assertRaises(ValueError):
                busy_intervals(db, DAY, DAY.replace(month=12), now=NOW)
            with self.assertRaises(ValueError):
                busy_intervals(db, DAY, DAY, now=datetime(2026, 11, 1))


if __name__ == "__main__":
    unittest.main()
