"""Migra SOLO la base PostgreSQL temporal para piloto, si se habilita expresamente.

Script de arranque de servicio independiente. No toca base Valhalla ni
registros de pacientes. Crea exclusivamente las tablas schema_holds.sql.
"""
import os
from pathlib import Path
import sys


def main():
    if os.environ.get("PSICOLOGIA_HOLDS_SCHEMA_BOOTSTRAP") != "true":
        print("PSICOLOGIA_PILOT_SCHEMA: bootstrap_disabled", flush=True)
        return 0
    if os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") != "true":
        print("PSICOLOGIA_PILOT_SCHEMA: requires_isolated_pilot", flush=True)
        return 1
    url = os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL", "")
    if not url:
        print("PSICOLOGIA_PILOT_SCHEMA: awaiting_database_configuration", flush=True)
        return 0
    try:
        import psycopg
        if not url.startswith(("postgresql://", "postgres://")):
            print("PSICOLOGIA_PILOT_SCHEMA: invalid_connection_protocol", flush=True)
            return 1
        sql = Path(__file__).with_name("schema_holds.sql").read_text("utf-8")
        with psycopg.connect(url, connect_timeout=10, application_name="psychology-pilot-bootstrap") as conn:
            conn.execute(sql)
        print("PSICOLOGIA_PILOT_SCHEMA: ready", flush=True)
        return 0
    except Exception as ex:
        print("PSICOLOGIA_PILOT_SCHEMA: error_type=" + type(ex).__name__, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
