"""Convierte instantánea PSC PostgreSQL a un archivo SQLite NUEVO, sin despliegue.

NO apunta al libro de compras `PSICOLOGIA_BILLING_DB`: crea una base aparte
para comprobar integridad en entorno de ensayo. El origen incluye datos privados.
Uso: python psicologia-api/tools/psc_import_snapshot_sqlite.py \
  --input /ruta/privada/psc_snapshot.json \
  --output /ruta/privada/psc_holds_staging.sqlite3
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from psc_pg_export_snapshot import COLUMNS, REVISION

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE psychology_booking_days (
    slot_date TEXT PRIMARY KEY NOT NULL
);
CREATE TABLE psychology_booking_holds (
    id TEXT PRIMARY KEY NOT NULL,
    cancel_secret_sha256 TEXT NOT NULL,
    slot_date TEXT NOT NULL REFERENCES psychology_booking_days(slot_date),
    starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
    service TEXT NOT NULL CHECK(service IN ('psicoterapia','empleabilidad','competencias')),
    status TEXT NOT NULL DEFAULT 'HELD'
       CHECK(status IN ('HELD','EXPIRED','CANCELLED','PAYMENT_PENDING',
                        'RECONCILIATION_REQUIRED','CONFIRMED')),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK(ends_at>starts_at)
);
CREATE INDEX psychology_holds_date_state ON psychology_booking_holds(slot_date,status,expires_at);
CREATE TABLE psychology_demo_payments (
    order_id TEXT PRIMARY KEY NOT NULL,
    hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
    session_id TEXT NOT NULL UNIQUE,
    token_sha256 TEXT UNIQUE,
    service TEXT NOT NULL CHECK(service IN ('psicoterapia','empleabilidad','competencias')),
    amount_clp INTEGER NOT NULL CHECK(amount_clp>0),
    test_recipient TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'STARTING'
       CHECK(state IN ('STARTING','PAYMENT_PENDING','COMMITTING','AUTHORIZED',
                       'REJECTED','CANCELLED','CALENDAR_PENDING','CONFIRMED',
                       'RECONCILIATION_REQUIRED')),
    calendar_event_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, verified_at TEXT,
    patient_name TEXT
);
CREATE INDEX psychology_demo_payments_states ON psychology_demo_payments(state,created_at);
CREATE TABLE psychology_demo_notifications (
    order_id TEXT NOT NULL REFERENCES psychology_demo_payments(order_id),
    recipient_role TEXT NOT NULL CHECK(recipient_role IN ('professional','patient')),
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK(status IN ('pending','sending','sent','failed')),
    attempted_at TEXT, sent_at TEXT, timezone_correction_sent_at TEXT,
    PRIMARY KEY(order_id,recipient_role)
);
CREATE TABLE psychology_reschedule_links (
    order_id TEXT PRIMARY KEY NOT NULL REFERENCES psychology_demo_payments(order_id),
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL, redeemed_at TEXT, revoked_at TEXT
);
CREATE TABLE psychology_reschedule_audit (
    id INTEGER PRIMARY KEY NOT NULL,
    order_id TEXT NOT NULL REFERENCES psychology_demo_payments(order_id),
    old_starts_at TEXT NOT NULL,
    new_starts_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'patient_link',
    UNIQUE(order_id)
);
CREATE TABLE psychology_google_oauth (
    key TEXT PRIMARY KEY NOT NULL,
    encrypted_refresh_token TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE psychology_fonasa_bookings (
    source_id TEXT PRIMARY KEY NOT NULL,
    hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
    patient_name TEXT NOT NULL,
    patient_email TEXT NOT NULL,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    redeemed_at TEXT,
    old_starts_at TEXT,
    new_starts_at TEXT
);
"""


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_snapshot(source):
    with source.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or data.get("revision") != REVISION:
        raise ValueError("Revisión de instantánea incompatible")
    if set(data.get("tables", {})) != set(COLUMNS):
        raise ValueError("Faltan o sobran tablas de la instantánea")
    for table, cols in COLUMNS.items():
        rows = data["tables"][table]
        if not isinstance(rows, list) or len(rows) != data.get("counts", {}).get(table):
            raise ValueError("Conteo incoherente en " + table)
        if any(not isinstance(row, dict) or set(row) != set(cols) for row in rows):
            raise ValueError("Columnas incompatibles en " + table)
    return data


def import_data(data, output):
    con = sqlite3.connect(output)
    try:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        for table, cols in COLUMNS.items():
            quoted_cols = ",".join('"' + col + '"' for col in cols)
            sql = ('INSERT INTO "' + table + '" (' + quoted_cols + ') VALUES ('
                   + ",".join("?" for _ in cols) + ")")
            con.executemany(sql, (
                tuple(row[col] for col in cols) for row in data["tables"][table]
            ))
            actual = con.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
            if actual != data["counts"][table]:
                raise RuntimeError("Importación incompleta: " + table)
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Las llaves foráneas no se pueden reconciliar")
        con.commit()
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Integridad SQLite inválida")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source, output = Path(args.input), Path(args.output)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise ValueError("El origen debe ser un archivo privado existente")
    if not output.is_absolute() or not output.parent.is_dir() or output.is_symlink():
        raise ValueError("El destino debe estar en directorio existente")
    if output.exists() or source.resolve() == output.resolve():
        raise FileExistsError("El destino ya existe o apunta al origen")
    data = load_snapshot(source)
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        import_data(data, output)
        print(json.dumps({"status": "SQLITE_STAGING_VERIFIED",
                          "counts": data["counts"],
                          "bytes": output.stat().st_size,
                          "sha256": file_sha256(output)}, ensure_ascii=False))
    except Exception:
        output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_SQLITE_IMPORT_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
