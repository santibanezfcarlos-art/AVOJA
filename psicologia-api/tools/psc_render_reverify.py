"""PSC: verifica solo las copias existentes en /var/data; NO vuelve a copiarlas.

No necesita URL ni credenciales PostgreSQL. No cambia bases de producción.
Descarga herramientas a /tmp desde commit inmutable de GitHub.
En caso de error muestra únicamente tabla/campo, nunca información de pacientes.
"""
from pathlib import Path
from urllib.request import urlopen
import os
import subprocess
import sys
import tempfile

REVISION = "b96f8ee93ce4b51ebb1eeefb7207a349d3890937"
BASE = ("https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/"
        + REVISION + "/psicologia-api/tools/")
MODULES = ("psc_verify_conversion.py", "psc_import_snapshot_sqlite.py",
           "psc_pg_export_snapshot.py")
ROOT = Path("/var/data/psc-migration-private")


def main():
    if not os.environ.get("RENDER") or not os.path.ismount("/var/data"):
        raise RuntimeError("Se requiere el Shell de Render con disco persistente")
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError("No existe el directorio privado de instantáneas")
    candidates = [
        p for p in ROOT.glob("snapshot-*")
        if p.is_dir() and not p.is_symlink()
        and (p / "psc_postgres_snapshot.json").is_file()
        and (p / "psc_postgres_staging.sqlite3").is_file()
    ]
    if not candidates:
        raise RuntimeError("No se encontraron exportación y ensayo completos")
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    print("PSC_VERIFY_EXISTING_DIR: " + chosen.name, flush=True)
    with tempfile.TemporaryDirectory(prefix="psc-verify-", dir="/tmp") as temp:
        temp = Path(temp)
        for filename in MODULES:
            with urlopen(BASE + filename, timeout=20) as response:
                data = response.read(256 * 1024 + 1)
                if len(data) < 50 or len(data) > 256 * 1024:
                    raise RuntimeError("Archivo de herramienta inesperado")
            (temp / filename).write_bytes(data)
        check = subprocess.run(
            [sys.executable, str(temp / "psc_verify_conversion.py"),
             "--snapshot", str(chosen / "psc_postgres_snapshot.json"),
             "--sqlite", str(chosen / "psc_postgres_staging.sqlite3")],
            capture_output=True, text=True, timeout=90, check=False,
            env={k: v for k, v in os.environ.items()
                 if k not in ("PSICOLOGIA_HOLDS_DATABASE_URL", "PGPASSWORD")}
        )
        if check.returncode:
            # El verificador revela solo los nombres conocidos de tabla/campo.
            print(check.stderr.strip(), file=sys.stderr)
            raise RuntimeError("No concuerdan las copias; no continuar migración")
        print(check.stdout.strip())
        print("PSC_EXISTING_SNAPSHOT_VERIFIED: ninguna fuente real modificada")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_EXISTING_VERIFY_STOP: " + type(exc).__name__ +
              " (no se modificaron las fuentes)", file=sys.stderr)
        sys.exit(1)
