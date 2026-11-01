"""PSC: encrypted offsite archive, created from already-verified staging copies.

No changes to source SQLite/PostgreSQL. The archive is AES-256-GCM encrypted
using a passphrase entered privately by the user, with PBKDF2-SHA256 KDF.
The passphrase is NOT printed, saved, transmitted, or logged.
All plaintext archive bytes stay in RAM; only ciphertext is written to disk.

Usage, in the paid Render Web Shell:
  python /tmp/psc_offsite_bundle.py --create
To validate an archive, locally or on Render:
  python /tmp/psc_offsite_bundle.py --verify /absolute/path/to/file.pscbackup

After creating it, securely TRANSFER it off Render (Render supports SCP and
Magic-Wormhole). Creating this archive alone is NOT an offsite backup.
"""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tarfile
from urllib.parse import quote

ROOT = Path("/var/data/psc-migration-private")
MANDATORY = ("psc_production_sqlite.sqlite3", "psc_postgres_snapshot.json",
             "psc_postgres_staging.sqlite3")
OPTIONAL = ("psc_postgres_full.dump",)
MAGIC = b"PSC-AESGCM-V1\n"
SALT_LEN = 16
NONCE_LEN = 12
KEY_ITERATIONS = 600_000
MAX_PLAINTEXT_BYTES = 32 * 1024 * 1024
MAX_CIPHERTEXT_BYTES = MAX_PLAINTEXT_BYTES + 1024
MIN_PASSPHRASE_LENGTH = 16


def crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("Falta libreria cryptography para cifrado autenticado")
    return AESGCM


def key_for(passphrase, salt):
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError("Se requiere frase secreta de al menos 16 caracteres")
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"),
                              salt, KEY_ITERATIONS, dklen=32)


def choose_snapshot(root=ROOT):
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("No existe directorio privado de instantaneas")
    candidates = []
    for folder in root.glob("snapshot-*"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        if all((folder / filename).is_file()
               and not (folder / filename).is_symlink()
               for filename in MANDATORY):
            candidates.append(folder)
    if not candidates:
        raise RuntimeError("No se encontro un ensayo PostgreSQL/SQLite completo")
    return max(candidates, key=lambda f: f.stat().st_mtime)


def digest_file(path):
    hash_ = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hash_.update(block)
    return hash_.hexdigest()


def sqlite_integrity(path):
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Copia SQLite no supera integrity_check")


def validate_snapshot(folder):
    for file in MANDATORY:
        p = folder / file
        if p.is_symlink() or not p.is_file():
            raise RuntimeError("Falta un archivo obligatorio")
    sqlite_integrity(folder / MANDATORY[0])
    sqlite_integrity(folder / MANDATORY[2])
    with (folder / MANDATORY[1]).open("r", encoding="utf-8") as stream:
        pg = json.load(stream)
    if pg.get("revision") != "psc-postgres-v1" or not isinstance(pg.get("counts"), dict):
        raise RuntimeError("Formato de instantanea no reconocido")
    for table, count in pg["counts"].items():
        if not isinstance(table, str) or not table.startswith("psychology_"):
            raise RuntimeError("Tabla inesperada")
        if type(count) is not int or count < 0:
            raise RuntimeError("Conteo PostgreSQL invalido")
        with sqlite3.connect(
            "file:" + quote(str((folder / MANDATORY[2]).resolve()), safe="/") + "?mode=ro",
            uri=True
        ) as conn:
            # El nombre no procede del cliente sino de nuestro esquema.
            if not table.replace("_", "").isalnum():
                raise RuntimeError("Nombre de tabla inesperado")
            if conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] != count:
                raise RuntimeError("Conteos diferentes en " + table)
    return pg["counts"]


def build_archive(folder):
    counts = validate_snapshot(folder)
    paths = [folder / name for name in MANDATORY]
    for name in OPTIONAL:
        if (folder / name).is_file() and not (folder / name).is_symlink():
            paths.append(folder / name)
    if sum(p.stat().st_size for p in paths) > MAX_PLAINTEXT_BYTES // 2:
        raise RuntimeError("Las copias superan el tamano seguro para empaquetado en memoria")
    manifest = {
        "format": "psc-offsite-manifest-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "PSC paid Render disk staging snapshot",
        "counts": counts,
        "files": {p.name: {"bytes": p.stat().st_size, "sha256": digest_file(p)}
                  for p in paths},
        "native_pg_dump_included": any(p.name == "psc_postgres_full.dump" for p in paths),
        "requires_isolated_restore": True,
    }
    archive_bytes = io.BytesIO()
    with tarfile.open(mode="w:gz", fileobj=archive_bytes, compresslevel=6) as tar:
        for p in paths:
            def safeinfo(info):
                if not info.isfile():
                    raise RuntimeError("Solo se admiten archivos ordinarios")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o600
                return info
            tar.add(p, arcname=p.name, recursive=False, filter=safeinfo)
        payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
        entry = tarfile.TarInfo("manifest.json")
        entry.size = len(payload)
        entry.mode = 0o600
        tar.addfile(entry, io.BytesIO(payload))
    result = archive_bytes.getvalue()
    if len(result) > MAX_PLAINTEXT_BYTES:
        raise RuntimeError("Archivo interno demasiado grande")
    for p in paths:
        if digest_file(p) != manifest["files"][p.name]["sha256"]:
            raise RuntimeError("Una copia cambio mientras se preparaba el respaldo")
    return result, manifest


def encrypt(data, passphrase, salt=None, nonce=None):
    AESGCM = crypto()
    salt = salt or secrets.token_bytes(SALT_LEN)
    nonce = nonce or secrets.token_bytes(NONCE_LEN)
    header = MAGIC + salt + nonce
    ciphertext = AESGCM(key_for(passphrase, salt)).encrypt(nonce, data, header)
    return header + ciphertext


def decrypt(file_bytes, passphrase):
    AESGCM = crypto()
    header_len = len(MAGIC) + SALT_LEN + NONCE_LEN
    if (len(file_bytes) < header_len + 16
            or len(file_bytes) > MAX_CIPHERTEXT_BYTES
            or not file_bytes.startswith(MAGIC)):
        raise ValueError("Archivo PSC desconocido o de tamano invalido")
    header = file_bytes[:header_len]
    salt = header[len(MAGIC):len(MAGIC) + SALT_LEN]
    nonce = header[-NONCE_LEN:]
    return AESGCM(key_for(passphrase, salt)).decrypt(nonce, file_bytes[header_len:], header)


def verify_archive(file_bytes, passphrase):
    plaintext = decrypt(file_bytes, passphrase)
    with tarfile.open(mode="r:gz", fileobj=io.BytesIO(plaintext)) as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if (len(set(names)) != len(names) or "manifest.json" not in names
                or any(not m.isfile() or "/" in m.name for m in members)):
            raise RuntimeError("Estructura interna inesperada")
        manifest = json.load(archive.extractfile("manifest.json"))
        if manifest.get("format") != "psc-offsite-manifest-v1":
            raise RuntimeError("Manifiesto de version desconocida")
        if set(manifest["files"]) != set(names) - {"manifest.json"}:
            raise RuntimeError("Archivos del respaldo distintos del manifiesto")
        if not set(MANDATORY).issubset(names):
            raise RuntimeError("Faltan copias obligatorias")
        for name, detail in manifest["files"].items():
            data = archive.extractfile(name).read(MAX_PLAINTEXT_BYTES + 1)
            if (len(data) != detail["bytes"]
                    or hashlib.sha256(data).hexdigest() != detail["sha256"]):
                raise RuntimeError("Hash inconsistente de respaldo")
        return {
            "status": "ENCRYPTED_BACKUP_VERIFIED",
            "files": sorted(manifest["files"]),
            "native_pg_dump_included": manifest["native_pg_dump_included"],
            "counts": manifest["counts"],
        }


def create():
    if not os.environ.get("RENDER") or not os.path.ismount("/var/data"):
        raise RuntimeError("Creacion solo desde Render con disco persistente")
    folder = choose_snapshot()
    plaintext, manifest = build_archive(folder)
    print("Esta copia incluira datos personales. Se cifrara con AES-256-GCM.")
    print("Usa una frase larga y UNICA. Conservala fuera de Render y del chat.")
    first = getpass("Frase secreta nueva (oculta): ")
    again = getpass("Repite la frase secreta (oculta): ")
    if first != again:
        raise ValueError("Las frases secretas no coinciden")
    payload = encrypt(plaintext, first)
    name = "psc-encrypted-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4) + ".pscbackup"
    out = ROOT / name
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        verified = verify_archive(out.read_bytes(), first)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    print(json.dumps({
        "status": verified["status"],
        "encrypted_file": str(out),
        "bytes": out.stat().st_size,
        "sha256": digest_file(out),
        "native_pg_dump_included": manifest["native_pg_dump_included"],
        "next_step": "transferir archivo cifrado fuera de Render y verificar en destino",
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true")
    mode.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.create:
        create()
    else:
        path = args.verify
        if not path.is_file() or path.is_symlink():
            raise ValueError("Archivo cifrado inexistente")
        password = getpass("Frase secreta de verificacion (oculta): ")
        print(json.dumps(verify_archive(path.read_bytes(), password), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # No imprimir argumentos, contrasenas, direcciones privadas o nombres.
        print("PSC_OFFSITE_STOP: " + type(exc).__name__ +
              " (las bases de produccion permanecen intactas)", file=sys.stderr)
        sys.exit(1)
