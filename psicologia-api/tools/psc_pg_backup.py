"""Copia consistente de PostgreSQL PSC, sin modificar datos.

Requiere pg_dump y pg_restore 18+ (PostgreSQL de Render es v18).
Se ejecuta localmente o en un entorno con conexión privada a Render.
NO subir respaldos a GitHub ni guardar URLs de conexión en archivos.
Ejemplo: python psicologia-api/tools/psc_pg_backup.py --output /ruta/privada/psc_20261101.dump
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def pg_env_and_args(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise ValueError("URL PostgreSQL inválida")
    dbname = unquote(parsed.path.lstrip("/"))
    username = unquote(parsed.username or "")
    if not dbname or not username:
        raise ValueError("Falta nombre de base de datos o usuario")
    env = os.environ.copy()
    if parsed.password is not None:
        env["PGPASSWORD"] = unquote(parsed.password)
    query = parse_qs(parsed.query)
    if query.get("sslmode", []):
        env["PGSSLMODE"] = query["sslmode"][0]
    args = ["--host", parsed.hostname, "--port", str(parsed.port or 5432),
            "--username", username, "--dbname", dbname]
    return env, args


def require_client(name):
    binary = shutil.which(name)
    if not binary:
        raise RuntimeError(name + " no está instalado")
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, check=True)
    match = re.search(r"\b(\d+)\.(?:\d+)", version.stdout)
    if not match or int(match.group(1)) < 18:
        raise RuntimeError(name + " debe ser versión 18 o superior")
    return binary


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Backup verificable de PostgreSQL PSC")
    parser.add_argument("--output", required=True, help="Ruta absoluta de archivo .dump privado")
    args = parser.parse_args()
    url = os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL", "")
    if not url:
        raise ValueError("Falta PSICOLOGIA_HOLDS_DATABASE_URL en el entorno")
    output = Path(args.output)
    if not output.is_absolute() or output.suffix != ".dump":
        raise ValueError("La salida debe ser una ruta absoluta terminada en .dump")
    if not output.parent.is_dir() or output.is_symlink():
        raise ValueError("Directorio inexistente o salida simbólica")
    dump, restore = require_client("pg_dump"), require_client("pg_restore")
    pg_env, connection_args = pg_env_and_args(url)

    # O_EXCL evita sobrescribir un respaldo anterior o seguir enlaces.
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        cmd = [dump, "--format=custom", "--no-owner", "--no-privileges",
               "--file", str(output), *connection_args]
        result = subprocess.run(cmd, env=pg_env, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError("pg_dump finalizó con error")
        result = subprocess.run([restore, "--list", str(output)],
                                capture_output=True, timeout=90)
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError("pg_restore no pudo leer el índice del archivo")
        if output.stat().st_size < 100:
            raise RuntimeError("El respaldo está vacío o incompleto")
        print(json.dumps({"status": "BACKUP_VERIFIED", "bytes": output.stat().st_size,
                          "sha256": checksum(output)}, indent=2))
    except Exception:
        # Nunca conservar un dump parcial confundiéndolo con copia válida.
        output.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_PG_BACKUP_FAILED: " + type(exc).__name__ + ": " +
              (str(exc) if isinstance(exc, (RuntimeError, ValueError, FileExistsError))
               else "Error de respaldo"), file=sys.stderr)
        sys.exit(1)
