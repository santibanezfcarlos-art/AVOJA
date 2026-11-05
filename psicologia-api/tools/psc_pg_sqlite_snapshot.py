"""Read-only PostgreSQL snapshot to encrypted SQLite transfer archive.

This module does not modify PostgreSQL or expose any HTTP endpoint.
It is intentionally NOT invoked at import time. Excludes non-public schemas
and requires an explicit, separate operator trigger.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet


def _encode(value):
    if isinstance(value, (date, datetime, time, Decimal, UUID)):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return {"__binary_base64__": base64.b64encode(bytes(value)).decode("ascii")}
    raise TypeError(f"Unsupported column type: {type(value).__name__}")


def export_snapshot():
    """Returns encrypted bytes + table count/row counts. Requires PG URL + key."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    url = os.environ["PSICOLOGIA_HOLDS_DATABASE_URL"]
    secret = os.environ["PSICOLOGIA_DEMO_BRIDGE_KEY"]
    if len(secret) < 40:
        raise RuntimeError("Missing strong bridge key")
    key = base64.urlsafe_b64encode(
        hashlib.sha256(b"psc-postgres-offsite-v1:" + secret.encode()).digest()
    )
    with tempfile.TemporaryDirectory(prefix="psc-snapshot-") as work:
        target = Path(work) / "snapshot.sqlite3"
        dest = sqlite3.connect(target)
        try:
            dest.execute("PRAGMA journal_mode=DELETE")
            dest.execute("CREATE TABLE snapshot_rows (source_table TEXT NOT NULL, row_index INTEGER NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(source_table,row_index))")
            dest.execute("CREATE TABLE snapshot_metadata (source_table TEXT PRIMARY KEY, source_count INTEGER NOT NULL, captured_at TEXT NOT NULL)")
            counts = {}
            with psycopg.connect(url, connect_timeout=10, row_factory=dict_row) as source:
                with source.transaction():
                    source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    relations = source.execute("""
                        SELECT tablename FROM pg_catalog.pg_tables
                        WHERE schemaname = 'public' ORDER BY tablename
                    """).fetchall()
                    for relation in relations:
                        table = relation["tablename"]
                        if not table.startswith("psychology_"):
                            raise RuntimeError("Unexpected public table, refusing incomplete export")
                        cursor = source.cursor(name="psc_snapshot_readonly")
                        cursor.execute(sql.SQL("SELECT * FROM public.{}").format(sql.Identifier(table)))
                        n = 0
                        while True:
                            batch = cursor.fetchmany(200)
                            if not batch:
                                break
                            dest.executemany(
                                "INSERT INTO snapshot_rows(source_table,row_index,payload_json) VALUES (?,?,?)",
                                [(table, n + i, json.dumps(dict(row), default=_encode, ensure_ascii=False))
                                 for i, row in enumerate(batch)]
                            )
                            n += len(batch)
                        cursor.close()
                        counts[table] = n
                        dest.execute(
                            "INSERT INTO snapshot_metadata VALUES (?,?,?)",
                            (table, n, datetime.now().astimezone().isoformat())
                        )
            dest.commit()
            if not {"psychology_booking_holds", "psychology_demo_payments", "psychology_google_oauth", "psychology_fonasa_bookings"}.issubset(counts):
                raise RuntimeError("Missing required tables")
            check = dest.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError("SQLite snapshot integrity failure")
        finally:
            dest.close()
        blob = target.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    ciphertext = Fernet(key).encrypt(blob)
    return ciphertext, {"tables": len(counts), "counts": counts, "plaintext_sha256": digest}
