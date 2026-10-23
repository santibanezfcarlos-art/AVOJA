"""Verificar duración de cupos usando períodos ocupados ficticios."""
import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app

class CalendarDurationTests(unittest.TestCase):
    def setUp(self):
        self.day = (datetime.now(ZoneInfo('America/Santiago')) + timedelta(days=3)).date()
        tz = ZoneInfo('America/Santiago')
        start = datetime.combine(self.day, time(10, 0), tzinfo=tz)
        self.patch = patch('main._load_busy_intervals', return_value=[(start, start+timedelta(minutes=30))])
        self.patch.start()
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()

    def request(self, duration):
        iso = self.day.isoformat()
        return self.client.get('/api/availability',query_string={'from':iso,'to':iso,'duration':str(duration)})

    def test_45_minutes_and_90_minutes_do_not_share_busy_mask(self):
        short = self.request(45)
        long = self.request(90)
        self.assertEqual(short.status_code,200)
        self.assertEqual(long.status_code,200)
        k = self.day.isoformat()
        a=short.get_json()
        b=long.get_json()
        self.assertEqual(a['duration_minutes'],45)
        self.assertEqual(b['duration_minutes'],90)
        self.assertNotIn('09:00',a['blocked'][k])
        self.assertIn('09:00',b['blocked'][k])
        self.assertIn('09:30',a['blocked'][k])
        self.assertNotIn('19:00',b['blocked'][k])

    def test_reject_invalid_duration(self):
        self.assertEqual(self.request(20).status_code,400)
        self.assertEqual(self.request('no').status_code,400)

if __name__ == '__main__':
    unittest.main()
