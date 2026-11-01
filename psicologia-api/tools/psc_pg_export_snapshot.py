"""Exportación privada de una instantánea PostgreSQL PSC a JSON para migración.

Solo consulta una transacción REPEATABLE READ/READ ONLY; no cambia PostgreSQL.
Incluye datos personales y tokens cifrados: archivo 0600, conservar en almacén
privado cifrado fuera de Render y NO subir al repositorio.
No sustituye a pg_dump: primero ejecutar respaldo íntegro y probar restauración.
Uso: python psicologia-api/tools/psc_pg_export_snapshot.py --output /ruta/privada/psc_snapshot.json
"""
import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

REVISION = "psc-postgres-v1"
COLUMNS = {
    "psychology_booking_days": ("slot_date",),
    "psychology_booking_holds": (
        "id", "cancel_secret_sha256", "slot_date", "starts_at", "ends_at",
        "service", "status", "expires_at", "created_at", "updated_at",
    ),
    "psychology_demo_payments": (
        "order_id", "hold_id", "session_id", "token_sha256", "service",
        "amount_clp", "test_recipient", "state", "calendar_event_id",
        "created_at", "updated_at", "verified_at", "patient_name",
    ),
    "psychology_demo_notifications": (
        "order_id", "recipient_role", "status", "attempted_at", "sent_at",
        "timezone_correction_sent_at",
    ),
    "psychology_reschedule_links": (
        "order_id", "token_sha256", "created_at", "redeemed_at", "revoked_at",
    ),
    "psychology_reschedule_audit": (
        "id", "order_id", "old_starts_at", "new_starts_at", "changed_at", "channel",
    ),
    "psychology_google_oauth": (
        "key", "encrypted_refresh_token", "updated_at",
    ),
    "psychology_fonasa_bookings": (
        "source_id", "hold_id", "patient_name", "patient_email",
        "token_sha256", "created_at", "redeemed_at",
        "old_starts_at", "new_starts_at",
    ),
}
KEYS = {
    "psychology_booking_days": ("slot_date",),
    "psychology_booking_holds": ("id",),
    "psychology_demo_payments": ("order_id",),
    "psychology_demo_notifications": ("order_id", "recipient_role"),
    "psychology_reschedule_links": ("order_id",),
    "psychology_reschedule_audit": ("id",),
    "psychology_google_oauth": ("key",),
    "psychology_fonasa_bookings": ("source_id",),
}


def encode(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("datetime PostgreSQL sin zona horaria")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, uuid.UUID)):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise ValueError("Tipo no reconocido en la instantánea")


def read_snapshot(url):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    payload = {"revision": REVISION, "captured_at_utc": None,
               "tables": {}, "counts": {}}
    with psycopg.connect(url, connect_timeout=12,
                         application_name="psc-export-snapshot",
                         row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30000ms'")
        payload["captured_at_utc"] = conn.execute(
            "SELECT transaction_timestamp() AS t"
        ).fetchone()["t"].astimezone(timezone.utc).isoformat()
        for table, expected in COLUMNS.items():
            actual = [r["column_name"] for r in conn.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s
                   ORDER BY ordinal_position""", (table,),
            ).fetchall()]
            if set(actual) != set(expected):
                raise ValueError("Esquema de PostgreSQL inesperado: " + table)
            query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                sql.SQL(",").join(map(sql.Identifier, expected)),
                sql.Identifier("public", table),
                sql.SQL(",").join(map(sql.Identifier, KEYS[table])),
            )
            records = conn.execute(query).fetchall()
            rows = [{c: encode(row[c]) for c in expected} for row in records]
            payload["tables"][table] = rows
            payload["counts"][table] = len(rows)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    url = os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        raise ValueError("No hay conexión PostgreSQL configurada")
    output = Path(args.output)
    if not output.is_absolute() or not output.parent.is_dir() or output.is_symlink():
        raise ValueError("Destino debe ser absoluto y en directorio existente")
    # O_EXCL: jamás pisar exportaciones antiguas.
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = read_snapshot(url)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "EXPORT_CREATED", "counts": data["counts"],
                          "bytes": output.stat().st_size,
                          "captured_at_utc": data["captured_at_utc"]}))
    except Exception:
        if fd is not None:
            os.close(fd)
        output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Nunca escribir detalles SQL ni URL de conexión en salida o logs.
        print("PSC_PG_EXPORT_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
