"""Inspección de capacidad del disco PSC sin modificar archivos ni datos.

Solo muestra disponibilidad, tamaño del archivo SQLite e integridad lógica;
no muestra rutas privadas, pacientes, compras ni detalles de boletas.
Uso: python psicologia-api/tools/psc_disk_preflight.py
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote


def inspect(mount="/var/data", path=None):
    store = Path(mount)
    db = Path(path or os.environ.get("PSICOLOGIA_BILLING_DB", ""))
    if not store.is_absolute() or not store.is_dir():
        raise ValueError("No hay carpeta persistente configurada")
    if os.environ.get("RENDER") and not os.path.ismount(str(store)):
        raise ValueError("El disco de Render no está montado")
    if not db.is_absolute() or not db.is_file() or db.is_symlink():
        raise ValueError("No existe la base SQLite configurada")
    if os.path.commonpath((str(store.resolve()), str(db.resolve()))) != str(store.resolve()):
        raise ValueError("SQLite no está en el disco esperado")
    stats = os.statvfs(str(store))
    available = stats.f_bavail * stats.f_frsize
    total = stats.f_blocks * stats.f_frsize
    uri = "file:" + quote(str(db.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as con:
        con.execute("PRAGMA query_only=ON")
        # quick_check es de solo lectura; no expone valores de pacientes.
        status = con.execute("PRAGMA quick_check").fetchone()[0]
    return {"status": "READY" if status == "ok" else "INTEGRITY_WARNING",
            "mounted": bool(os.path.ismount(str(store))),
            "capacity_bytes": total, "available_bytes": available,
            "billing_db_bytes": db.stat().st_size,
            "sqlite_quick_check": status == "ok"}


if __name__ == "__main__":
    try:
        print(json.dumps(inspect(), sort_keys=True))
    except Exception as exc:
        print("PSC_DISK_PREFLIGHT_FAILED: " + type(exc).__name__,
              file=sys.stderr)
        sys.exit(1)
