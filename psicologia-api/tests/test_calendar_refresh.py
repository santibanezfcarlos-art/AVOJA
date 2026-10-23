"""Pruebas sin calendario real ni datos de pacientes."""
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.dict(os.environ, {"GOOGLE_CALENDAR_ICS_URL": ""}):
    import main


class CalendarRefreshTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GOOGLE_CALENDAR_ICS_URL":
            "https://calendar.google.com/calendar/ical/test/basic.ics"})
        self.env.start()
        self.wait = patch.object(main, "CALENDAR_WAIT_SECONDS", 0.03)
        self.wait.start()
        main._feed_cache.update(at=0.0, events=[])
        main._refreshing = False
        main._retry_after = 0.0
        main._refresh_done.clear()

    def tearDown(self):
        main._refresh_done.wait(1)
        self.wait.stop()
        self.env.stop()

    def test_slow_read_is_shared_and_requests_finish_without_stale_data(self):
        release = threading.Event()
        def slow_read(url):
            release.wait(2)
            return []
        main._feed_cache.update(at=time.monotonic()-1000, events=["stale"])
        d = datetime.now(main.TZ).date().isoformat()
        def request(_):
            with main.app.test_client() as client:
                response = client.get(f"/api/availability?from={d}&to={d}&duration=90")
                return response.status_code, response.get_json(), response.headers.get("Retry-After")
        with patch.object(main, "_read_busy_intervals", side_effect=slow_read) as read:
            try:
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=4) as pool:
                    responses = list(pool.map(request, range(4)))
                self.assertLess(time.monotonic()-started, 1)
                self.assertEqual(read.call_count, 1)
                for status, body, retry in responses:
                    self.assertEqual(status, 503)
                    self.assertEqual(body["status"], "refreshing")
                    self.assertNotIn("blocked", body)
                    self.assertEqual(retry, "15")
            finally:
                release.set()
                self.assertTrue(main._refresh_done.wait(1))
            self.assertEqual(main._load_busy_intervals(), [])
            self.assertEqual(read.call_count, 1)

    def test_failed_read_has_cooldown_then_recovers(self):
        with patch.object(main, "_read_busy_intervals", side_effect=OSError("test")) as read:
            with self.assertRaises(main.CalendarRefreshingError):
                main._load_busy_intervals()
            self.assertTrue(main._refresh_done.wait(1))
            with self.assertRaises(main.CalendarRefreshingError):
                main._load_busy_intervals()
            self.assertEqual(read.call_count, 1)
        main._retry_after = 0
        with patch.object(main, "_read_busy_intervals", return_value=[]):
            self.assertEqual(main._load_busy_intervals(), [])

    def test_duration_and_overlap_preserved(self):
        today = datetime.now(main.TZ).replace(hour=11, minute=0, second=0, microsecond=0)
        main._feed_cache.update(at=time.monotonic(), events=[(today, today.replace(hour=12))])
        d = today.date().isoformat()
        with main.app.test_client() as client:
            for duration in (45, 60, 90):
                r = client.get(f"/api/availability?from={d}&to={d}&duration={duration}")
                self.assertEqual(r.status_code, 200)
                blocked = r.get_json()["blocked"][d]
                self.assertIn("11:00", blocked)
                self.assertNotIn("12:00", blocked)
                self.assertEqual("10:00" in blocked, duration == 90)


if __name__ == "__main__":
    unittest.main()
