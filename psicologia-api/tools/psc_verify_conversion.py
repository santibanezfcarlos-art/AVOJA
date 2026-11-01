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
            # PostgreSQL puede ordenar texto según ICU; SQLite por defecto
            # usa orden binario. Nunca emparejar por posición tras ORDER BY.
            actual_rows = con.execute('SELECT * FROM "' + table + '"').fetchall()
            expected_rows = data["tables"][table]
            if len(actual_rows) != len(expected_rows):
                raise RuntimeError("Conteo diferente en " + table)
            keys = KEYS[table]
            actual_by_key = {}
            for row in actual_rows:
                if set(row.keys()) != set(columns):
                    raise RuntimeError("Columnas inesperadas en " + table)
                key = tuple(row[k] for k in keys)
                if key in actual_by_key:
                    raise RuntimeError("Llave duplicada en " + table)
                actual_by_key[key] = row
            expected_by_key = {}
            for row in expected_rows:
                key = tuple(row[k] for k in keys)
                if key in expected_by_key:
                    raise RuntimeError("Llave duplicada en instantánea de " + table)
                expected_by_key[key] = row
            if set(actual_by_key) != set(expected_by_key):
                # Nunca exponer identificadores, correos o tokens privados.
                raise RuntimeError("Llaves diferentes en " + table)
            for key, row_b in expected_by_key.items():
                row_a = actual_by_key[key]
                for column in columns:
                    if row_a[column] != row_b[column]:
                        # Solo el nombre del campo es seguro; no los valores.
                        raise RuntimeError("Campo diferente en " + table + "." + column)
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
        # Los errores son redactados por compare(): solo tabla y columna; nunca valores.\n        reason = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__\n        print("PSC_RECONCILIATION_FAILED: " + reason, file=sys.stderr)
        sys.exit(1)
