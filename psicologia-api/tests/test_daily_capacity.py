import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
import main
from daily_capacity import full_days


class DailyCapacityTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.now(main.TZ).replace(hour=9, minute=0, second=0, microsecond=0)

    def intervals(self, hours):
        return [(self.start.replace(hour=h), self.start.replace(hour=h)+timedelta(minutes=45)) for h in hours]

    def test_duplicate_sources_with_different_end_times_count_once(self):
        busy = self.intervals([9, 11])
        busy.append((busy[0][0], busy[0][1]+timedelta(minutes=15)))
        self.assertEqual(full_days(busy), set())
        self.assertEqual(full_days(busy+self.intervals([15])), {self.start.date()})

    def test_mixed_calendar_and_paid_reservations_close_every_service(self):
        day = self.start.date().isoformat()
        with (patch.object(main, '_load_busy_intervals', return_value=self.intervals([9, 11])),
              patch('webpay_demo_bridge.enabled', return_value=True),
              patch('webpay_demo_bridge.busy_intervals', return_value=self.intervals([15])),
              patch('billing.configured', return_value=False)):
            for duration in (45, 60, 90):
                response = main.app.test_client().get(f'/api/availability?from={day}&to={day}&duration={duration}')
                self.assertEqual(response.status_code, 200)
                blocked = response.json['blocked'][day]
                self.assertIn('17:00', blocked)
                self.assertIn('09:00', blocked)

    def test_two_bookings_leave_other_times_available(self):
        day = self.start.date().isoformat()
        with (patch.object(main, '_load_busy_intervals', return_value=self.intervals([9, 11])),
              patch('webpay_demo_bridge.enabled', return_value=False),
              patch('billing.configured', return_value=False)):
            response = main.app.test_client().get(f'/api/availability?from={day}&to={day}&duration=45')
            self.assertNotIn('17:00', response.json['blocked'][day])
