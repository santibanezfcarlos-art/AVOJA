"""PSC SQLite state: staged, disabled by default. ONLY main API owns /var/data."""
import os, re, sqlite3, tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from contextlib import contextmanager
DB=Path("/var/data/psc-state/holds.sqlite3")
RECOVERY=Path("/var/data/psc-postgres-recovery")
TABLES=set("psychology_booking_days psychology_booking_holds psychology_demo_payments psychology_demo_notifications psychology_reschedule_links psychology_reschedule_audit psychology_google_oauth psychology_fonasa_bookings".split())
ACTIVE={"HELD","PAYMENT_PENDING","RECONCILIATION_REQUIRED"}

class StateUnavailable(RuntimeError): pass

def _counts(c):
    names={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'psychology_%'")}
    if names!=TABLES or c.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or c.execute("PRAGMA foreign_key_check").fetchall():
        raise StateUnavailable("Invalid schema or integrity")
    return {n:c.execute('SELECT count(*) FROM "'+n+'"').fetchone()[0] for n in names}

def seed(*,source=None,target=None,isolated=False):
    if isolated:
        if source is None or target is None: raise StateUnavailable("Test paths required")
        src,dst=Path(source),Path(target)
    else:
        if source is not None or target is not None: raise StateUnavailable("Custom production paths forbidden")
        if not os.path.ismount("/var/data") or os.environ.get("PSC_SQLITE_SEED_EXPLICITLY_ALLOWED")!="true":
            raise StateUnavailable("Production seed disabled")
        copies=sorted(RECOVERY.glob("psc-pg-*.sqlite3"),reverse=True)
        if not copies: raise StateUnavailable("Missing recovery copy")
        src,dst=copies[0],DB
    if not src.is_file() or dst.exists(): raise StateUnavailable("Missing source or destination exists")
    dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with sqlite3.connect("file:"+str(src)+"?mode=ro",uri=True) as c: original=_counts(c)
    fd,tmp=tempfile.mkstemp(prefix=".psc-",dir=dst.parent);os.close(fd)
    try:
        with sqlite3.connect("file:"+str(src)+"?mode=ro",uri=True) as c:
            with sqlite3.connect(tmp) as out:c.backup(out)
        with sqlite3.connect(tmp) as c: copied=_counts(c)
        if copied!=original:raise StateUnavailable("Row parity mismatch")
        os.chmod(tmp,0o600);os.link(tmp,dst)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return {"tables":len(original),"rows":sum(original.values())}

@contextmanager
def connect(*,test_path=None,write=False):
    path=Path(test_path) if test_path is not None else DB
    if test_path is None and (os.environ.get("PSC_SQLITE_STATE_ENABLED")!="true" or not os.path.ismount("/var/data")):
        raise StateUnavailable("SQLite live mode disabled")
    if not path.is_file():raise StateUnavailable("State file missing")
    c=sqlite3.connect(path,timeout=15,isolation_level=None);c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON");c.execute("PRAGMA busy_timeout=15000")
    try:
        if write:c.execute("BEGIN IMMEDIATE")
        else:c.execute("PRAGMA query_only=ON")
        yield c
        if write:c.commit()
    except Exception:
        if write:c.rollback()
        raise
    finally:c.close()

def utc(v):
    if isinstance(v,str):v=datetime.fromisoformat(v.replace("Z","+00:00"))
    if not isinstance(v,datetime) or v.tzinfo is None:raise ValueError("Timezone required")
    return v.astimezone(timezone.utc)

def windows(day1,day2,*,test_path=None,now=None):
    now=utc(now or datetime.now(timezone.utc))
    with connect(test_path=test_path) as c:
        rows=c.execute("SELECT starts_at,ends_at,status,expires_at FROM psychology_booking_holds WHERE slot_date BETWEEN ? AND ?",(day1.isoformat(),day2.isoformat())).fetchall()
    return [(utc(r["starts_at"]),utc(r["ends_at"])) for r in rows if r["status"]=="CONFIRMED" or (r["status"] in ACTIVE and utc(r["expires_at"])>now)]

def reserve(*,hold_id,secret_hash,service,start,end,test_path=None,now=None,max_daily=3,expiry_seconds=600):
    now=utc(now or datetime.now(timezone.utc));start,end=utc(start),utc(end)
    day=start.astimezone(ZoneInfo("America/Santiago")).date()
    if start>=end or start<=now or end.astimezone(ZoneInfo("America/Santiago")).date()!=day:raise ValueError("Invalid window")
    if not re.fullmatch(r"[0-9a-f-]{36}",str(hold_id)) or not re.fullmatch(r"[0-9a-f]{64}",secret_hash) or service not in ("psicoterapia","empleabilidad","competencias"):raise ValueError("Invalid hold")
    with connect(test_path=test_path,write=True) as c:
        c.execute("INSERT OR IGNORE INTO psychology_booking_days(slot_date) VALUES(?)",(str(day),))
        rows=c.execute("SELECT starts_at,ends_at,status,expires_at FROM psychology_booking_holds WHERE slot_date=?",(str(day),)).fetchall()
        busy=[(utc(r["starts_at"]),utc(r["ends_at"])) for r in rows if r["status"]=="CONFIRMED" or (r["status"] in ACTIVE and utc(r["expires_at"])>now)]
        if len(busy)>=max_daily or any(start<y and x<end for x,y in busy):return False
        c.execute("INSERT INTO psychology_booking_holds (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(str(hold_id),secret_hash,str(day),start.isoformat(),end.isoformat(),service,"HELD",(now+timedelta(seconds=expiry_seconds)).isoformat(),now.isoformat(),now.isoformat()))
    return True
