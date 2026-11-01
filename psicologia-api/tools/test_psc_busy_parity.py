"""Offline parity checks against a synthetic frozen PSC export."""
from pathlib import Path
from datetime import datetime, timezone
import json
import sqlite3
import tempfile
import unittest

from psc_import_snapshot_sqlite import import_data
from psc_shadow_busy_parity import check
from test_psc_snapshot_conversion import fixture


class ParityTests(unittest.TestCase):
    def setup_copies(self, tmp):
        root = Path(tmp)
        snapshot = fixture()
        snapshot["tables"]["psychology_booking_holds"][0]["expires_at"] = "2026-10-01T00:00:00+00:00"
        snapshot["counts"] = {key:len(value) for key,value in snapshot["tables"].items()}
        input_file = root / "pg.json"
        output_file = root / "sqlite.db"
        input_file.write_text(json.dumps(snapshot), encoding="utf-8")
        import_data(snapshot, output_file)
        return input_file, output_file

    def test_snapshot_and_sqlite_busy_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            pg, db = self.setup_copies(tmp)
            result = check(pg, db)
            self.assertEqual(result["status"], "PSC_BUSY_PARITY_OK")
            self.assertGreater(result["days_checked"], 0)
            self.assertFalse(result["live_calendar_tested"])

    def test_detects_sqlite_status_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            pg, db = self.setup_copies(tmp)
            with sqlite3.connect(db) as con:
                con.execute("UPDATE psychology_booking_holds SET status='CANCELLED'")
            with self.assertRaises(RuntimeError):
                check(pg, db)


if __name__ == "__main__":
    unittest.main()
