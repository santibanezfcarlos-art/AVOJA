"""Pruebas offline de conversión PostgreSQL PSC -> SQLite con datos FICTICIOS."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

from psc_pg_export_snapshot import COLUMNS, REVISION, encode

TOOLS = Path(__file__).parent
HOLD = "11111111-1111-4111-8111-111111111111"
FONASA = "FONASA-TEST-1"
STAMP = "2026-11-01T18:00:00+00:00"
HOUR_START = "2026-11-12T18:00:00+00:00"
HOUR_END = "2026-11-12T18:45:00+00:00"
HASH = "a" * 64


def fixture():
    tables = {table: [] for table in COLUMNS}
    tables["psychology_booking_days"] = [{"slot_date": "2026-11-12"}]
    tables["psychology_booking_holds"] = [{
        "id": HOLD, "cancel_secret_sha256": HASH,
        "slot_date": "2026-11-12", "starts_at": HOUR_START, "ends_at": HOUR_END,
        "service": "psicoterapia", "status": "CONFIRMED",
        "expires_at": HOUR_END, "created_at": STAMP, "updated_at": STAMP,
    }]
    tables["psychology_fonasa_bookings"] = [{
        "source_id": FONASA, "hold_id": HOLD,
        "patient_name": "Paciente Ficticio",
        "patient_email": "ejemplo@invalid.test",
        "token_sha256": "b" * 64,
        "created_at": STAMP, "redeemed_at": None,
        "old_starts_at": None, "new_starts_at": None,
    }]
    tables["psychology_google_oauth"] = [{
        "key": "refresh", "encrypted_refresh_token": "CIPHERTEXT-EXAMPLE",
        "updated_at": STAMP,
    }]
    return {"revision": REVISION, "captured_at_utc": STAMP,
            "tables": tables, "counts": {k: len(v) for k, v in tables.items()}}


class ConvertTests(unittest.TestCase):
    def test_timestamps_are_utc(self):
        self.assertEqual(encode(datetime(2026, 11, 1, 10, tzinfo=timezone.utc)), STAMP.replace("18:00", "10:00"))
        self.assertEqual(encode(uuid.UUID(HOLD)), HOLD)
        with self.assertRaises(ValueError):
            encode(datetime(2026, 11, 1, 10))

    def test_preserves_fonasa_link_and_encrypted_oauth_without_touching_source(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            original = folder / "snapshot.json"
            destination = folder / "converted.sqlite3"
            original.write_text(json.dumps(fixture()), encoding="utf-8")
            before = original.read_bytes()
            run = subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "SQLITE_STAGING_VERIFIED")
            self.assertEqual(original.read_bytes(), before)
            reconciliation = subprocess.run(
                [sys.executable, str(TOOLS / "psc_verify_conversion.py"),
                 "--snapshot", str(original), "--sqlite", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertEqual(reconciliation.returncode, 0, reconciliation.stderr)
            self.assertEqual(json.loads(reconciliation.stdout)["status"], "EXACT_RECONCILIATION_OK")
            with sqlite3.connect(destination) as con:
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
                token, hold = con.execute(
                    "SELECT token_sha256,hold_id FROM psychology_fonasa_bookings"
                ).fetchone()
                self.assertEqual(token, "b" * 64)
                self.assertEqual(hold, HOLD)
                self.assertEqual(con.execute(
                    "SELECT encrypted_refresh_token FROM psychology_google_oauth"
                ).fetchone()[0], "CIPHERTEXT-EXAMPLE")
            second = subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertNotEqual(second.returncode, 0)

    def test_reconciliation_ignores_postgres_text_collation_order(self):
        # Simula una ordenación PostgreSQL lingüística distinta del orden
        # binario SQLite. Los registros deben compararse por llave, no por fila.
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            original = folder / "snapshot.json"
            destination = folder / "converted.sqlite3"
            payload = fixture()
            extra_hold = dict(payload["tables"]["psychology_booking_holds"][0])
            extra_hold["id"] = "22222222-2222-4222-8222-222222222222"
            payload["tables"]["psychology_booking_holds"].append(extra_hold)
            second = dict(payload["tables"]["psychology_fonasa_bookings"][0])
            second["source_id"] = "a-lower"
            second["hold_id"] = extra_hold["id"]
            second["token_sha256"] = "c" * 64
            payload["tables"]["psychology_fonasa_bookings"][0]["source_id"] = "Z-upper"
            # Orden inverso al cotejo por BINARY de SQLite.
            payload["tables"]["psychology_fonasa_bookings"] = [
                second, payload["tables"]["psychology_fonasa_bookings"][0]
            ]
            payload["counts"] = {name: len(rows) for name, rows in payload["tables"].items()}
            original.write_text(json.dumps(payload), encoding="utf-8")
            imported = subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            verified = subprocess.run(
                [sys.executable, str(TOOLS / "psc_verify_conversion.py"),
                 "--snapshot", str(original), "--sqlite", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(json.loads(verified.stdout)["status"], "EXACT_RECONCILIATION_OK")

    def test_reconciliation_detects_modified_staging_data(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            original = folder / "snapshot.json"
            destination = folder / "converted.sqlite3"
            original.write_text(json.dumps(fixture()), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, check=True
            )
            with sqlite3.connect(destination) as con:
                con.execute("UPDATE psychology_google_oauth SET encrypted_refresh_token='INVALID'")
            check = subprocess.run(
                [sys.executable, str(TOOLS / "psc_verify_conversion.py"),
                 "--snapshot", str(original), "--sqlite", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertNotEqual(check.returncode, 0)
            self.assertNotIn("CIPHERTEXT", check.stderr)

    def test_rejects_broken_foreign_key_and_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            original = folder / "snapshot.json"
            destination = folder / "converted.sqlite3"
            bad = fixture()
            bad["tables"]["psychology_fonasa_bookings"][0]["hold_id"] = "22222222-2222-4222-8222-222222222222"
            original.write_text(json.dumps(bad), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(destination.exists())

    def test_rejects_unknown_column_before_writing(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            original = folder / "snapshot.json"
            destination = folder / "converted.sqlite3"
            bad = fixture()
            bad["tables"]["psychology_google_oauth"][0]["unexpected"] = "value"
            original.write_text(json.dumps(bad), encoding="utf-8")
            run = subprocess.run(
                [sys.executable, str(TOOLS / "psc_import_snapshot_sqlite.py"),
                 "--input", str(original), "--output", str(destination)],
                capture_output=True, text=True, check=False
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
