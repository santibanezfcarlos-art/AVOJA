"""Offline recipient tests; no emails or external API calls."""
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from ttlock_recipients import recipients_for_tomorrow

class Tests(unittest.TestCase):
    def test_next_day_only_confirmed_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"sample.sqlite3"
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE users (id INTEGER PRIMARY KEY,email TEXT);
                    CREATE TABLE bookings (id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,start TEXT);
                    INSERT INTO users VALUES (1,'a@example.org'),(2,'b@example.org'),(3,'a@example.org');
                    INSERT INTO bookings VALUES
                    (1,1,'confirmed','2026-10-22T13:00:00+00:00'),
                    (2,1,'confirmed','2026-10-22T14:00:00+00:00'),
                    (3,2,'cancelled','2026-10-22T15:00:00+00:00'),
                    (4,3,'confirmed','2026-10-22T16:00:00+00:00'),
                    (5,2,'confirmed','2026-10-23T14:00:00+00:00');
                """)
            day, recipients=recipients_for_tomorrow(str(path),datetime(2026,10,21,22,tzinfo=ZoneInfo("America/Santiago")))
            self.assertEqual(day.isoformat(),"2026-10-22")
            self.assertEqual(recipients,["a@example.org"])

if __name__=="__main__":
    unittest.main()
