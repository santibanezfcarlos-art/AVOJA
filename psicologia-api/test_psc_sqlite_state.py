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
CREATE TABLE psychology_fonasa_bookings(source_id TEXT PRIMARY KEY,hold_id TEXT NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),patient_name TEXT NOT NULL,patient_email TEXT NOT NULL,token_sha256 TEXT NOT NULL UNIQUE,created_at TEXT,redeemed_at TEXT,old_starts_at TEXT,new_starts_at TEXT);
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

def test_fonasa():
 from psc_sqlite_fonasa import register,prepare_change,commit_change,eligibility,FonasaStateConflict
 with tempfile.TemporaryDirectory() as d:
  source,target=Path(d)/"source.sqlite",Path(d)/"state.sqlite"
  with sqlite3.connect(source) as con:con.executescript(SCHEMA)
  seed(source=source,target=target,isolated=True)
  now=datetime.now(timezone.utc)
  start=(now+timedelta(days=5)).astimezone(ZoneInfo("America/Santiago")).replace(hour=11,minute=0,second=0,microsecond=0)
  end=start+timedelta(minutes=45)
  a=hashlib.sha256(b"fonasa-a").hexdigest();b=hashlib.sha256(b"fonasa-b").hexdigest()
  one,two=str(uuid.uuid4()),str(uuid.uuid4())
  assert register(source_id="fonasa-test-00001",hold_id=one,token_sha256=a,name="Test Uno",email="uno@example.invalid",start=start,end=end,test_path=target)=="REGISTERED"
  assert register(source_id="fonasa-test-00001",hold_id=one,token_sha256=a,name="Test Uno",email="uno@example.invalid",start=start,end=end,test_path=target)=="REGISTERED"
  assert register(source_id="fonasa-test-00002",hold_id=two,token_sha256=b,name="Test Dos",email="dos@example.invalid",start=start+timedelta(hours=2),end=end+timedelta(hours=2),test_path=target)=="REGISTERED"
  new_start=start+timedelta(hours=4);new_end=new_start+timedelta(minutes=45)
  assert prepare_change(source_id="fonasa-test-00001",token_sha256=a,new_start=new_start,new_end=new_end,test_path=target,now=now)=="PREPARED"
  try:
   prepare_change(source_id="fonasa-test-00002",token_sha256=b,new_start=new_start,new_end=new_end,test_path=target,now=now)
  except FonasaStateConflict:pass
  else:raise AssertionError("Two concurrent destination claims were allowed")
  assert commit_change(source_id="fonasa-test-00001",token_sha256=a,new_start=new_start,new_end=new_end,test_path=target)=="CONFIRMED"
  assert commit_change(source_id="fonasa-test-00001",token_sha256=a,new_start=new_start,new_end=new_end,test_path=target)=="CONFIRMED"
  assert eligibility(token_sha256=a,test_path=target,now=now)=="NOT_ELIGIBLE"

if __name__=="__main__":test();test_fonasa();print("PSC_SQLITE_TEST_OK")
