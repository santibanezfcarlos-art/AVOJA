"""Reconciliación exacta de una instantánea PSC con su SQLite de ensayo.

No escribe ni en PostgreSQL ni en SQLite. Compara TODOS los registros,
incluidos hashes y tokens cifrados, pero JAMÁS revela sus valores.
Uso: python psicologia-api/tools/psc_verify_conversion.py \
 --snapshot /ruta/privada/psc_snapshot.json \
 --sqlite /ruta/privada/psc_holds_staging.sqlite3
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from psc_pg_export_snapshot import COLUMNS, KEYS
from psc_import_snapshot_sqlite import load_snapshot


def compare(snapshot_path, db_path):
    data = load_snapshot(snapshot_path)
    if not db_path.is_file() or db_path.is_symlink():
        raise ValueError("SQLite de ensayo inexistente o no permitido")
    uri = "file:" + quote(str(db_path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Integridad SQLite falló")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Referencias rotas en SQLite")
        for table, columns in COLUMNS.items():
            order = ", ".join('"' + col + '"' for col in KEYS[table])
            actual = con.execute('SELECT * FROM "' + table + '" ORDER BY ' + order).fetchall()
            expected = data["tables"][table]
            if len(actual) != len(expected):
                raise RuntimeError("No coinciden los conteos de " + table)
            for row_a, row_b in zip(actual, expected):
                if set(row_a.keys()) != set(columns):
                    raise RuntimeError("Columnas inesperadas en " + table)
                if any(row_a[col] != row_b[col] for col in columns):
                    # Nunca exponer ni valores ni identificadores de pacientes.
                    raise RuntimeError("Contenido distinto en " + table)
    return data["counts"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--sqlite", required=True)
    args = parser.parse_args()
    snapshot_path, db_path = Path(args.snapshot), Path(args.sqlite)
    if not snapshot_path.is_absolute() or not db_path.is_absolute():
        raise ValueError("Se necesitan rutas absolutas")
    results = compare(snapshot_path, db_path)
    print(json.dumps({"status": "EXACT_RECONCILIATION_OK", "counts": results},
                     ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_RECONCILIATION_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
