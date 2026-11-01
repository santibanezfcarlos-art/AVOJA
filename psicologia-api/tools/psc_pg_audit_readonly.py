"""PSC: inventario de PostgreSQL sin datos personales ni escrituras.

Uso: PSICOLOGIA_HOLDS_DATABASE_URL=... python psicologia-api/tools/psc_pg_audit_readonly.py
Ejecutar en un entorno con conectividad privada a la base de Render.
No modificar el resultado ni publicarlo si contiene identificadores inesperados.
"""
import json
import os
import sys
from collections import OrderedDict

TABLES = (
    "psychology_booking_days",
    "psychology_booking_holds",
    "psychology_demo_payments",
    "psychology_demo_notifications",
    "psychology_reschedule_links",
    "psychology_reschedule_audit",
    "psychology_google_oauth",
    "psychology_fonasa_bookings",
)


def main():
    url = os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Falta PSICOLOGIA_HOLDS_DATABASE_URL (no se imprime su valor)")
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    output = OrderedDict()
    with psycopg.connect(
        url, connect_timeout=10, application_name="psc-migration-audit-readonly",
        row_factory=dict_row,
    ) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '10000ms'")
        output["database_size_bytes"] = conn.execute(
            "SELECT pg_database_size(current_database()) AS n"
        ).fetchone()["n"]
        output["tables"] = {}
        for table in TABLES:
            present = conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS found", ("public." + table,)
            ).fetchone()["found"]
            if not present:
                output["tables"][table] = {"exists": False}
                continue
            count = conn.execute(
                sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier("public", table))
            ).fetchone()["n"]
            output["tables"][table] = {"exists": True, "rows": count}
        for table in ("psychology_booking_holds", "psychology_demo_payments",
                      "psychology_demo_notifications"):
            if output["tables"][table]["exists"]:
                column = "status" if table != "psychology_demo_payments" else "state"
                result = conn.execute(
                    sql.SQL("SELECT {} AS label, COUNT(*) AS n FROM {} GROUP BY {} ORDER BY {}").format(
                        sql.Identifier(column), sql.Identifier("public", table),
                        sql.Identifier(column), sql.Identifier(column)
                    )
                ).fetchall()
                output["tables"][table]["state_counts"] = {
                    str(row["label"]): row["n"] for row in result
                }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Evitar imprimir una excepción del driver que pudiera contener el DSN.
        print("PSC_AUDIT_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
