"""PSC restoration rehearsal. Runs only against copies; no live DB writes.

Reconciles all eight tables, token digest shapes, timestamps, and FK integrity.
Creates a throwaway SQLite restoration in /tmp to rehearse a transaction and
rollback. This is NOT a live integration test or native pg_dump restore.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from urllib.parse import quote
import uuid

TABLES = {
    "psychology_booking_days": 1,
    "psychology_booking_holds": 10,
    "psychology_demo_payments": 13,
    "psychology_demo_notifications": 6,
    "psychology_reschedule_links": 5,
    "psychology_reschedule_audit": 6,
    "psychology_google_oauth": 3,
    "psychology_fonasa_bookings": 9,
}

def open_readonly(path):
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)

def validate(snapshot, staging, billing):
    sources = (snapshot, staging, billing)
    if any(not p.is_file() or p.is_symlink() for p in sources):
        raise RuntimeError("Copia inexistente o enlace simbólico")
    if len({p.resolve() for p in sources}) != 3:
        raise RuntimeError("Los respaldos deben ser archivos distintos")
    with snapshot.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("revision") != "psc-postgres-v1":
        raise RuntimeError("Formato de instantánea inesperado")
    if set(data.get("tables", {})) != set(TABLES) or set(data.get("counts", {})) != set(TABLES):
        raise RuntimeError("Tablas incompletas")
    counts = {}
    with open_readonly(staging) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite restauración corrupta")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Referencias foráneas rotas")
        for table, size in TABLES.items():
            columns = db.execute('PRAGMA table_info("' + table + '")').fetchall()
            if len(columns) != size:
                raise RuntimeError("Esquema diferente: " + table)
            count = db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
            if count != data["counts"][table] or count != len(data["tables"][table]):
                raise RuntimeError("Conteo diferente: " + table)
            counts[table] = count
        for table in ("psychology_fonasa_bookings", "psychology_reschedule_links"):
            for (digest,) in db.execute('SELECT token_sha256 FROM "' + table + '"'):
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise RuntimeError("Hash privado inválido: " + table)
        for (a, b) in db.execute("SELECT starts_at,ends_at FROM psychology_booking_holds"):
            start, end = datetime.fromisoformat(a), datetime.fromisoformat(b)
            if start.tzinfo is None or end.tzinfo is None or start >= end:
                raise RuntimeError("Fecha sin zona o intervalo inválido")
        if db.execute("SELECT count(*) FROM psychology_fonasa_bookings f LEFT JOIN psychology_booking_holds h ON f.hold_id=h.id WHERE h.id IS NULL").fetchone()[0]:
            raise RuntimeError("Reserva Fonasa sin retención")
        if db.execute("SELECT count(*) FROM psychology_google_oauth WHERE length(encrypted_refresh_token)=0").fetchone()[0]:
            raise RuntimeError("OAuth cifrado vacío")
    with open_readonly(billing) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite de compras corrupto")
        existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"billing_orders", "production_payments"}.issubset(existing):
            raise RuntimeError("No se hallaron tablas de compras de producción")

    # Restore into a newly created, isolated temp DB and test reversible changes.
    with TemporaryDirectory(prefix="psc-rehearsal-") as td:
        disposable = Path(td) / "restored.sqlite3"
        with open_readonly(staging) as source, sqlite3.connect(disposable) as dest:
            source.backup(dest)
        with sqlite3.connect(disposable) as clone:
            clone.execute("PRAGMA foreign_keys=ON")
            if clone.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Fallo restauración independiente")
            day = "2099-01-12"
            start = datetime(2099, 1, 12, 10, 0, tzinfo=timezone.utc)
            end = start + timedelta(minutes=45)
            ident = str(uuid.uuid4())
            clone.execute("BEGIN IMMEDIATE")
            clone.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES (?)", (day,))
            clone.execute("""INSERT INTO psychology_booking_holds
                (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,
                 expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ident,hashlib.sha256(b"synthetic-only").hexdigest(),day,
                 start.isoformat(),end.isoformat(),"psicoterapia","HELD",
                 end.isoformat(),start.isoformat(),start.isoformat()))
            if clone.execute("SELECT count(*) FROM psychology_booking_holds WHERE id=?", (ident,)).fetchone()[0] != 1:
                raise RuntimeError("La escritura de ensayo no quedó almacenada")
            clone.rollback()
            if clone.execute("SELECT count(*) FROM psychology_booking_holds WHERE id=?", (ident,)).fetchone()[0] != 0:
                raise RuntimeError("Rollback de ensayo falló")
    return {"status": "PSC_ISOLATED_RESTORE_OK", "counts": counts,
            "staging_copy_recovered": True, "simulated_transaction_rollback": True,
            "production_sources_modified": False, "external_archive_decryption_tested": False,
            "google_and_payments_live_tested": False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--billing", type=Path, required=True)
    args = parser.parse_args()
    if any(not p.is_absolute() for p in (args.snapshot, args.staging, args.billing)):
        raise RuntimeError("Rutas absolutas requeridas")
    if args.staging.name != "psc_postgres_staging.sqlite3" or args.billing.name != "psc_production_sqlite.sqlite3":
        raise RuntimeError("Solo se aceptan archivos de respaldos aislados")
    print(json.dumps(validate(args.snapshot,args.staging,args.billing),ensure_ascii=False))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_RESTORE_DRILL_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
