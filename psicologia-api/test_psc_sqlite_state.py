"""Offline test for SQLite state module; never touches Render/Transbank."""
import sqlite3,tempfile,uuid,hashlib,concurrent.futures
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from psc_sqlite_state import seed,reserve,windows
SCHEMA = """
CREATE TABLE psychology_booking_days(slot_date TEXT PRIMARY KEY);
CREATE TABLE psychology_booking_holds(id TEXT PRIMARY KEY,cancel_secret_sha256 TEXT,slot_date TEXT REFERENCES psychology_booking_days(slot_date),starts_at TEXT,ends_at TEXT,service TEXT,status TEXT,expires_at TEXT,created_at TEXT,updated_at TEXT);
CREATE TABLE psychology_demo_payments(order_id TEXT PRIMARY KEY);
CREATE TABLE psychology_demo_notifications(order_id TEXT);
CREATE TABLE psychology_reschedule_links(order_id TEXT);
CREATE TABLE psychology_reschedule_audit(id INTEGER);
CREATE TABLE psychology_google_oauth(key TEXT);
CREATE TABLE psychology_fonasa_bookings(source_id TEXT);
"""
def test():
 with tempfile.TemporaryDirectory() as d:
  a,b=Path(d)/"a.sqlite",Path(d)/"b.sqlite"
  with sqlite3.connect(a) as c:c.executescript(SCHEMA)
  assert seed(source=a,target=b,isolated=True)["tables"]==8
  now=datetime.now(timezone.utc)
  start=(now+timedelta(days=3)).astimezone(ZoneInfo("America/Santiago")).replace(hour=11,minute=0,second=0,microsecond=0)
  def one(_):
   return reserve(hold_id=str(uuid.uuid4()),secret_hash=hashlib.sha256(b"x").hexdigest(),service="psicoterapia",start=start,end=start+timedelta(minutes=45),now=now,test_path=b)
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(one,range(8)))
  assert results.count(True)==1 and results.count(False)==7
  assert len(windows(start.date(),start.date(),test_path=b,now=now))==1
if __name__=="__main__":test();print("PSC_SQLITE_TEST_OK")
