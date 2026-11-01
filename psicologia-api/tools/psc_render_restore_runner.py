"""One-shot Render shell launcher for an isolated recovery drill; read-only sources.

Uses the preexisting staging copies and does NOT need PostgreSQL credentials,
OAuth tokens, or encryption passphrase. Not a live production cutover.
"""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen

ROOT = Path("/var/data/psc-migration-private")
COMMIT = "43b769d3bec5a0ec00be1b8586956bb7dd8315da"
URL = ("https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/"
       + COMMIT + "/psicologia-api/tools/psc_restore_drill.py")

def main():
    if not os.environ.get("RENDER") or not os.path.ismount("/var/data"):
        raise RuntimeError("No se encuentra Shell del backend pagado")
    if ROOT.is_symlink() or not ROOT.is_dir():
        raise RuntimeError("No hay directorio privado de copias")
    files = ("psc_postgres_snapshot.json", "psc_postgres_staging.sqlite3",
             "psc_production_sqlite.sqlite3")
    folders = [folder for folder in ROOT.glob("snapshot-*")
               if folder.is_dir() and not folder.is_symlink()
               and all((folder / name).is_file() and not (folder / name).is_symlink() for name in files)]
    if not folders:
        raise RuntimeError("No se localizaron las tres copias necesarias")
    folder = max(folders, key=lambda item: item.stat().st_mtime)
    with TemporaryDirectory(prefix="psc-restore-runner-", dir="/tmp") as tmp:
        target = Path(tmp) / "psc_restore_drill.py"
        with urlopen(URL, timeout=20) as reply:
            content = reply.read(128 * 1024 + 1)
            if len(content) < 100 or len(content) > 128 * 1024:
                raise RuntimeError("Herramienta de ensayo inválida")
        target.write_bytes(content)
        result = subprocess.run(
            [sys.executable, str(target),
             "--snapshot", str(folder / files[0]),
             "--staging", str(folder / files[1]),
             "--billing", str(folder / files[2])],
            capture_output=True, text=True, timeout=120, check=False,
            env={k: v for k, v in os.environ.items()
                 if k not in ("PSICOLOGIA_HOLDS_DATABASE_URL","PGPASSWORD")}
        )
        if result.returncode:
            raise RuntimeError("Ensayo aislado rechazado: bases reales sin cambios")
        print(result.stdout.strip())
        print("PSC_RESTORE_RUNNER_FINISHED: no se modificó producción")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_RESTORE_RUNNER_STOP: " + type(exc).__name__ +
              " (sin cambios en producción)", file=sys.stderr)
        sys.exit(1)
