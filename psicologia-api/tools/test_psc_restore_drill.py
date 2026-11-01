"""Dry-run tests for PSC restoration, using invented data only."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from psc_import_snapshot_sqlite import import_data
from test_psc_snapshot_conversion import fixture

TOOLS = Path(__file__).parent

def make_copies(directory):
    directory = Path(directory)
    data = fixture()
    snapshot = directory / "psc_postgres_snapshot.json"
    staging = directory / "psc_postgres_staging.sqlite3"
    billing = directory / "psc_production_sqlite.sqlite3"
    snapshot.write_text(json.dumps(data), encoding="utf-8")
    import_data(data, staging)
    with sqlite3.connect(billing) as db:
        db.execute("CREATE TABLE billing_orders (id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE production_payments (id TEXT PRIMARY KEY)")
    return snapshot, staging, billing

class RestoreDrillTests(unittest.TestCase):
    def run_drill(self, paths):
        a, b, c = paths
        return subprocess.run(
            [sys.executable, str(TOOLS / "psc_restore_drill.py"),
             "--snapshot", str(a), "--staging", str(b), "--billing", str(c)],
            capture_output=True, text=True, check=False
        )

    def test_isolated_restore_and_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_copies(tmp)
            initial = [p.read_bytes() for p in paths]
            result = self.run_drill(paths)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "PSC_ISOLATED_RESTORE_OK")
            self.assertTrue(report["simulated_transaction_rollback"])
            self.assertFalse(report["production_sources_modified"])
            self.assertEqual([p.read_bytes() for p in paths], initial)

    def test_detects_corrupted_record_without_exposing_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_copies(tmp)
            with sqlite3.connect(paths[1]) as db:
                db.execute("UPDATE psychology_fonasa_bookings SET token_sha256=?", ("invalid",))
            result = self.run_drill(paths)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Paciente Ficticio", result.stderr)
            self.assertNotIn("ejemplo@invalid.test", result.stderr)

if __name__ == "__main__":
    unittest.main()
