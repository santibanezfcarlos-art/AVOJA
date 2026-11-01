"""Pruebas locales, sin conexión a Render y sin modificar servicios reales.

Ejecutar: python -m unittest psicologia-api/tools/test_psc_backups.py
"""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).parent


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PSCBackupTests(unittest.TestCase):
    def test_postgres_url_keeps_password_out_of_process_args(self):
        pg = load_module("psc_pg_backup.py", "psc_pg_backup")
        environment, args = pg.pg_env_and_args(
            "postgresql://reader:p%40ss%3Aword@db.internal:5432/holds?sslmode=require"
        )
        self.assertEqual(environment["PGPASSWORD"], "p@ss:word")
        self.assertEqual(environment["PGSSLMODE"], "require")
        self.assertIn("db.internal", args)
        self.assertIn("holds", args)
        self.assertNotIn("p@ss:word", " ".join(args))

    def test_sqlite_snapshot_is_consistent_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "source.sqlite3"
            dest = Path(folder) / "snapshot.sqlite3"
            with sqlite3.connect(src) as db:
                db.execute("CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO example(value) VALUES (?)", ("ejemplo",))
            env = os.environ.copy()
            env.pop("RENDER", None)
            run = subprocess.run(
                [sys.executable, str(TOOLS / "psc_sqlite_backup.py"),
                 "--source", str(src), "--output", str(dest)],
                capture_output=True, text=True, env=env, check=False
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "BACKUP_VERIFIED")
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT value FROM example").fetchone()[0], "ejemplo")
            with sqlite3.connect(src) as db:
                db.execute("INSERT INTO example(value) VALUES (?)", ("posterior",))
            with sqlite3.connect(dest) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM example").fetchone()[0], 1)
            repeated = subprocess.run(
                [sys.executable, str(TOOLS / "psc_sqlite_backup.py"),
                 "--source", str(src), "--output", str(dest)],
                capture_output=True, text=True, env=env, check=False
            )
            self.assertNotEqual(repeated.returncode, 0)


if __name__ == "__main__":
    unittest.main()
