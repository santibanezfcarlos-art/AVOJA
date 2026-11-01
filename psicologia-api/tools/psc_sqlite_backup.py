"""Respaldo consistente SQLite PSC sin interrumpir escrituras ni tocar la fuente.

Usa la API sqlite3.backup para preservar una instantánea transaccional.
El resultado contiene datos privados: almacenarlo cifrado y fuera de Render.
Ejemplo: python psicologia-api/tools/psc_sqlite_backup.py --output /ruta/privada/psc_sqlite_20261101.sqlite3
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Backup SQLite consistente PSC")
    parser.add_argument("--source", help="Archivo SQLite; por defecto PSICOLOGIA_BILLING_DB")
    parser.add_argument("--output", required=True, help="Archivo de salida absoluto")
    args = parser.parse_args()
    source = Path(args.source or os.environ.get("PSICOLOGIA_BILLING_DB", ""))
    output = Path(args.output)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise ValueError("Ruta origen SQLite absoluta, existente y no simbólica requerida")
    if os.environ.get("RENDER") and not str(source).startswith("/var/data/"):
        raise ValueError("En Render el origen debe estar en /var/data/")
    if not output.is_absolute() or not output.parent.is_dir() or output.is_symlink():
        raise ValueError("Ruta de salida absoluta, con directorio existente y sin enlaces")
    if source.resolve() == output.resolve():
        raise ValueError("Origen y destino no pueden coincidir")
    if output.exists():
        raise FileExistsError("El respaldo de destino ya existe")

    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        # SQLite backup proporciona una copia consistente con escrituras concurrentes.
        with sqlite3.connect(str(source), timeout=30) as src:
            with sqlite3.connect(str(output), timeout=30) as dst:
                src.backup(dst, pages=256, sleep=0.25)
                integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError("PRAGMA integrity_check rechazó el respaldo")
        print(json.dumps({"status": "BACKUP_VERIFIED", "bytes": output.stat().st_size,
                          "sha256": checksum(output)}, indent=2))
    except Exception:
        output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_SQLITE_BACKUP_FAILED: " + type(exc).__name__ + ": " +
              (str(exc) if isinstance(exc, (RuntimeError, ValueError, FileExistsError))
               else "Error de respaldo"), file=sys.stderr)
        sys.exit(1)
