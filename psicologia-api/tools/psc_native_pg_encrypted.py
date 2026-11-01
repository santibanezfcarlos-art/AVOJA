"""Native PostgreSQL backup and encrypted transfer artifact.

Runs only from the PAID Render shell on the mounted /var/data disk.
Reads Postgres with pg_dump, checks pg_restore TOC, encrypts with OpenSSL
and verifies decryption by SHA-256 before securely removing the raw dump.
Does not change service configuration, live tables, payments or calendars.
Must be transferred OFF Render and restored independently before cutover.

Requires: pg_dump/pg_restore >=18, openssl, Python3.
"""
from datetime import datetime, timezone
from getpass import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path("/var/data/psc-migration-private")
EXPECTED_TABLES = {
    "psychology_booking_days", "psychology_booking_holds",
    "psychology_demo_payments", "psychology_demo_notifications",
    "psychology_reschedule_links", "psychology_reschedule_audit",
    "psychology_google_oauth", "psychology_fonasa_bookings",
}
OPENSSL_ITER = "600000"


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def require_client(name):
    location = shutil.which(name)
    if not location:
        raise RuntimeError("Missing PostgreSQL CLI " + name)
    result = subprocess.run([location, "--version"], check=True,
                            capture_output=True, text=True, timeout=10)
    import re
    match = re.search(r"(?:PostgreSQL\)\s*)(\d+)(?:\.\d+)?", result.stdout)
    if not match or int(match.group(1)) < 18:
        raise RuntimeError("Client version less than 18: " + name)
    return location


def safe_connection(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise ValueError("URL de PostgreSQL inválida")
    username = unquote(parsed.username or "")
    database = unquote(parsed.path.lstrip("/"))
    if not username or not database or not parsed.password:
        raise ValueError("URL incompleta")
    args = ["--host", parsed.hostname, "--port", str(parsed.port or 5432),
            "--username", username, "--dbname", database]
    env = os.environ.copy()
    env["PGPASSWORD"] = unquote(parsed.password)
    values = parse_qs(parsed.query)
    if "sslmode" in values:
        env["PGSSLMODE"] = values["sslmode"][0]
    return args, env


def openssl_pass_command(parameters, passphrase, *, capture=False):
    # Only an ephemeral inherited pipe carries the secret. Never command args,
    # logs, shell history, persistent environment variables, or repo.
    fd_read, fd_write = os.pipe()
    try:
        process = subprocess.Popen(
            ["openssl", "enc", *parameters, "-pass", "fd:" + str(fd_read)],
            pass_fds=(fd_read,),
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        os.close(fd_read)
    try:
        os.write(fd_write, passphrase.encode("utf-8") + b"\n")
    finally:
        os.close(fd_write)
    if capture:
        digest = hashlib.sha256()
        with process.stdout as output:
            for chunk in iter(lambda: output.read(65536), b""):
                digest.update(chunk)
        code = process.wait(timeout=120)
        if code:
            raise RuntimeError("No se pudo verificar el descifrado")
        return digest.hexdigest()
    code = process.wait(timeout=120)
    if code:
        raise RuntimeError("No se pudo cifrar el respaldo")
    return None


def main():
    os.umask(0o077)
    if not os.environ.get("RENDER") or not os.path.ismount("/var/data"):
        raise RuntimeError("Se requiere Shell pagada de Render con disco montado")
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise RuntimeError("Carpeta privada existente no disponible")
    if not sys.stdin.isatty():
        raise RuntimeError("Se necesita terminal interactiva")
    dump_exe, restore_exe = require_client("pg_dump"), require_client("pg_restore")
    if not shutil.which("openssl"):
        raise RuntimeError("OpenSSL no disponible")
    # Leave a safe space margin: no need to fill the 1 GB shared disk.
    stats = os.statvfs(str(ROOT))
    free = stats.f_bavail * stats.f_frsize
    if free < 256 * 1024 * 1024:
        raise RuntimeError("Queda poco espacio libre en el disco")
    url = getpass("Internal Database URL de PostgreSQL (oculta): ")
    connection_args, pg_env = safe_connection(url)
    # drop our copy, no accidental printing in exceptions
    del url
    print("PSC: realizando pg_dump consistente y privado...", flush=True)
    folder = Path(tempfile.mkdtemp(prefix="native-", dir=str(ROOT)))
    folder.chmod(0o700)
    raw = folder / "psc_postgres_full.dump"
    ciphertext = folder / "psc_postgres_full.dump.enc"
    try:
        cmd = [dump_exe, "--format=custom", "--no-owner", "--no-privileges",
               "--file", str(raw), *connection_args]
        result = subprocess.run(cmd, env=pg_env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=600, check=False)
        pg_env.pop("PGPASSWORD", None)
        if result.returncode or not raw.is_file() or raw.stat().st_size < 100:
            raise RuntimeError("pg_dump no generó una copia válida")
        raw.chmod(0o600)
        toc = subprocess.run([restore_exe, "--list", str(raw)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=90, check=False)
        if toc.returncode:
            raise RuntimeError("pg_restore no reconoció el respaldo")
        toc_text = toc.stdout.decode("utf-8", "replace")
        missing = [table for table in EXPECTED_TABLES
                   if not any(line.strip().startswith(";") is False
                              and ("TABLE " + table + " ") in line
                              for line in toc_text.splitlines())]
        if missing:
            # Table names are known constants, no patient data.
            raise RuntimeError("Faltan tablas en el respaldo nativo: " + ", ".join(missing))
        raw_hash = sha256_file(raw)
        first = getpass("Contraseña para este respaldo (oculta, mínimo 16 caracteres): ")
        second = getpass("Repítela exactamente (oculta): ")
        if first != second or len(first) < 16 or any(x in first for x in ("\n", "\r", "\0")):
            raise RuntimeError("Las contraseñas no coinciden o no cumplen mínimo")
        openssl_pass_command(
            ["-aes-256-cbc", "-salt", "-pbkdf2", "-iter", OPENSSL_ITER,
             "-in", str(raw), "-out", str(ciphertext)],
            first,
        )
        if not ciphertext.is_file() or ciphertext.stat().st_size < 100:
            raise RuntimeError("El respaldo cifrado está vacío")
        verified_hash = openssl_pass_command(
            ["-d", "-aes-256-cbc", "-pbkdf2", "-iter", OPENSSL_ITER,
             "-in", str(ciphertext)],
            first, capture=True,
        )
        if raw_hash != verified_hash:
            raise RuntimeError("La copia descifrada no coincide con el original")
        ciphertext.chmod(0o600)
        print(json.dumps({
            "status": "PSC_NATIVE_PG_ENCRYPTED_VERIFIED",
            "file": str(ciphertext),
            "bytes": ciphertext.stat().st_size,
            "sha256": sha256_file(ciphertext),
            "tables_verified": len(EXPECTED_TABLES),
            "restored_to_separate_postgres": False,
            "production_modified": False,
        }, ensure_ascii=False), flush=True)
    finally:
        # Never leave a full plaintext backup on the production disk.
        raw.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_NATIVE_PG_STOP: " + type(exc).__name__ +
              " (las bases de producción no han sido modificadas)",
              file=sys.stderr)
        sys.exit(1)
