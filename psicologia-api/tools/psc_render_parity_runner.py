"""PSC: run frozen busy-slot parity and inspect PG backup client availability.

Read-only for existing copies, no credentials, no SQL writes.
Must run from paid Render service with persistent /var/data.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

COMMIT = "776da2142c553b0ea47b6feebc30202d3717f3e3"
BASE = ("https://raw.githubusercontent.com/santibanezfcarlos-art/AVOJA/"
        + COMMIT + "/psicologia-api/tools/")
MODULES = ["psc_shadow_busy_parity.py","psc_busy_reader.py",
           "psc_import_snapshot_sqlite.py","psc_pg_export_snapshot.py"]
ROOT = Path("/var/data/psc-migration-private")


def main():
    if not os.environ.get("RENDER") or not os.path.ismount("/var/data"):
        raise RuntimeError("Render persistent disk required")
    candidates = [x for x in ROOT.glob("snapshot-*")
                  if x.is_dir() and not x.is_symlink()
                  and (x / "psc_postgres_snapshot.json").is_file()
                  and (x / "psc_postgres_staging.sqlite3").is_file()]
    if not candidates:
        raise RuntimeError("No prior validated staging copies")
    snapshot = max(candidates, key=lambda x: x.stat().st_mtime)
    with tempfile.TemporaryDirectory(prefix="psc-parity-", dir="/tmp") as tmp:
        workspace = Path(tmp)
        for module in MODULES:
            with urlopen(BASE + module, timeout=20) as response:
                data = response.read(128 * 1024 + 1)
                if len(data) < 100 or len(data) > 128 * 1024:
                    raise RuntimeError("Unexpected utility size")
            (workspace / module).write_bytes(data)
        run = subprocess.run(
            [sys.executable, str(workspace / "psc_shadow_busy_parity.py"),
             str(snapshot / "psc_postgres_snapshot.json"),
             str(snapshot / "psc_postgres_staging.sqlite3")],
            capture_output=True, text=True, timeout=90, check=False,
            env={k:v for k,v in os.environ.items()
                 if k not in ("PSICOLOGIA_HOLDS_DATABASE_URL","PGPASSWORD")}
        )
        if run.returncode:
            raise RuntimeError("Busy-slot snapshot parity failed")
        print(run.stdout.strip())
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    pg_client_ok = False
    if pg_dump and pg_restore:
        for executable in (pg_dump,pg_restore):
            result = subprocess.run([executable,"--version"],capture_output=True,text=True,timeout=10)
            if result.returncode:
                break
            if "PostgreSQL) 18." not in result.stdout and "PostgreSQL) 19." not in result.stdout:
                break
        else:
            pg_client_ok = True
    print(json.dumps({
        "status": "PSC_PARITY_AND_BACKUP_PREFLIGHT_OK",
        "native_pg_client_18_plus_available": pg_client_ok,
        "native_pg_backup_generated": False,
        "live_postgres_modified": False,
        "public_calendar_modified": False
    }))
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_PARITY_RUNNER_FAILED: " + type(exc).__name__ +
              " (no production changes)", file=sys.stderr)
        sys.exit(1)
