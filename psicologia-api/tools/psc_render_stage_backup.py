"""PSC: copia y conversión de ensayo desde la Shell PAGADA de Render.

NO altera PostgreSQL ni SQLite de producción y NO cambia endpoints ni deploys.
Guarda datos privados en /var/data/psc-migration-private (modo 0700/0600).
NO es un respaldo externo: antes del corte hay que sacar copia CIFRADA
fuera de Render y probar una restauración.
"""
from getpass import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen


REVISION = "7527087b04c41f6e9d67a3336a97e06e056acb88"
BASE = ("https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/"
        + REVISION + "/psicologia-api/tools/")
MODULES = (
    "psc_pg_export_snapshot.py",
    "psc_import_snapshot_sqlite.py",
    "psc_verify_conversion.py",
    "psc_sqlite_backup.py",
    "psc_pg_backup.py",
)
MIN_FREE_AFTER = 256 * 1024 * 1024


def fail(message):
    print("PSC_STAGE_STOP: " + message, file=sys.stderr)
    sys.exit(1)


def download_modules(dest):
    for name in MODULES:
        with urlopen(BASE + name, timeout=20) as response:
            data = response.read(256 * 1024 + 1)
            if len(data) < 50 or len(data) > 256 * 1024:
                raise RuntimeError("Tamaño de herramienta inesperado")
        target = dest / name
        target.write_bytes(data)
        target.chmod(0o600)


def get_psycopg():
    dependency_dir = "/tmp/psc-migration-deps"
    os.makedirs(dependency_dir, exist_ok=True)
    if dependency_dir not in sys.path:
        sys.path.insert(0, dependency_dir)
    try:
        import psycopg
    except ImportError:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "--target", dependency_dir,
             "psycopg[binary]>=3.2,<4"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("No fue posible instalar dependencia temporal")
        import psycopg
    return psycopg, dependency_dir


def run_step(tool_dir, module, args, env):
    result = subprocess.run(
        [sys.executable, str(tool_dir / module), *args],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=600, check=False,
    )
    if result.returncode:
        # Nunca divulgar stderr del driver ni URL de conexión, por prudencia.
        raise RuntimeError("Fallo en " + module)
    print(result.stdout.strip())


def main():
    os.umask(0o077)
    if not os.environ.get("RENDER"):
        fail("Esta operación solo debe ejecutarse en Shell de Render")
    mount = Path("/var/data")
    path = Path(os.environ.get("PSICOLOGIA_BILLING_DB", "/not-configured"))
    if not mount.is_dir() or not os.path.ismount(mount):
        fail("El disco persistente no está montado")
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        fail("Base SQLite principal no localizada")
    if os.path.commonpath((str(mount.resolve()), str(path.resolve()))) != str(mount.resolve()):
        fail("La base SQLite no está dentro del disco contratado")
    if not sys.stdin.isatty():
        fail("Se requiere Shell interactiva para ocultar la credencial")

    with tempfile.TemporaryDirectory(prefix="psc-stage-tools-") as temp:
        tools = Path(temp)
        download_modules(tools)
        psycopg, dependency_dir = get_psycopg()
        print("Se solicitará nuevamente la Internal Database URL de PostgreSQL.")
        print("No la publiques en el chat ni en capturas de pantalla.")
        url = getpass("URL interna PostgreSQL (oculta): ")
        if not url.startswith(("postgresql://", "postgres://")):
            fail("No se introdujo una URL PostgreSQL válida")
        with psycopg.connect(url, connect_timeout=12,
                             application_name="psc-stage-preflight-readonly") as con:
            con.execute("SET TRANSACTION READ ONLY")
            pg_bytes = con.execute(
                "SELECT pg_database_size(current_database())"
            ).fetchone()[0]
        stats = os.statvfs(mount)
        free = stats.f_bavail * stats.f_frsize
        sqlite_bytes = path.stat().st_size
        required = pg_bytes * 4 + sqlite_bytes * 2 + MIN_FREE_AFTER
        if free <= required:
            fail("Espacio insuficiente para cuatro copias y margen de seguridad")
        print(json.dumps({"storage_preflight": "READY", "free_bytes": free,
                          "postgres_size_bytes": pg_bytes,
                          "required_with_reserve_bytes": required}))

        root = mount / "psc-migration-private"
        if root.is_symlink():
            fail("Ruta de respaldo inesperada")
        root.mkdir(mode=0o700, exist_ok=True)
        root.chmod(0o700)
        destination = Path(tempfile.mkdtemp(prefix="snapshot-", dir=str(root)))
        destination.chmod(0o700)
        env = os.environ.copy()
        env["PSICOLOGIA_HOLDS_DATABASE_URL"] = url
        env["PYTHONPATH"] = dependency_dir

        print("PSC: respaldando SQLite sin modificar el origen...")
        run_step(tools, "psc_sqlite_backup.py",
                 ["--output", str(destination / "psc_production_sqlite.sqlite3")],
                 env)
        print("PSC: exportando PostgreSQL en transacción de solo lectura...")
        run_step(tools, "psc_pg_export_snapshot.py",
                 ["--output", str(destination / "psc_postgres_snapshot.json")], env)
        print("PSC: construyendo SQLite de ensayo separado...")
        run_step(tools, "psc_import_snapshot_sqlite.py",
                 ["--input", str(destination / "psc_postgres_snapshot.json"),
                  "--output", str(destination / "psc_postgres_staging.sqlite3")],
                 env)
        print("PSC: reconciliando todos los campos y registros...")
        run_step(tools, "psc_verify_conversion.py",
                 ["--snapshot", str(destination / "psc_postgres_snapshot.json"),
                  "--sqlite", str(destination / "psc_postgres_staging.sqlite3")],
                 env)

        full_dump = "NO_REALIZADO_CLIENTE_NO_DISPONIBLE"
        if shutil.which("pg_dump") and shutil.which("pg_restore"):
            try:
                run_step(tools, "psc_pg_backup.py",
                         ["--output", str(destination / "psc_postgres_full.dump")],
                         env)
                full_dump = "ARCHIVO_VALIDADO_RESTAURACION_AUN_PENDIENTE"
            except Exception:
                full_dump = "NO_REALIZADO_O_FALLIDO"

        print(json.dumps({"result": "PSC_STAGING_COMPLETED",
                          "private_dir": str(destination),
                          "pg_dump": full_dump,
                          "live_sources_modified": False,
                          "needs_offsite_encrypted_copy": True,
                          "needs_isolated_restore": True}, ensure_ascii=False))
        # Nunca persistir la URL ni su password.


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_STAGE_STOP: " + type(exc).__name__ +
              " (las bases de producción no se han modificado)", file=sys.stderr)
        sys.exit(1)
