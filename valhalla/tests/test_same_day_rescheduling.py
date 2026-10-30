from datetime import datetime, timedelta
from unittest.mock import patch
import unittest
import test_booking_rules
from app import CHILE, Problem, stamp


class SameDayRescheduling(unittest.TestCase):
    slot = test_booking_rules.BookingRules.slot
    def setUp(self):
        test_booking_rules.BookingRules.setUp(self)
        self.clock = datetime(2026, 10, 28, 9, tzinfo=CHILE)
        clock = patch('app.now', side_effect=lambda: self.clock)
        clock.start()
        self.addCleanup(clock.stop)
        self.start = stamp(self.clock.replace(hour=15))
        with patch('app.now', return_value=self.clock-timedelta(days=2)):
            self.bid = self.store.reserve(self.uid, self.slot())

    # This class reuses the account fixture, not its unrelated test cases.
    def target(self, hour=18, days=0):
        return dict(self.slot(), start=stamp((self.clock+timedelta(days=days)).replace(hour=hour)))

    def available(self, hour, uid=None, bid=None):
        data = self.store.availability('2026-10-28', uid=uid or self.uid, reschedule_id=bid)
        return next(row for row in data['slots'] if row['label']==f'{hour:02d}:00')['boxes'][0]['available']

    def test_move_today_preserves_balance_and_uses_only_change(self):
        self.assertTrue(self.available(18, bid=self.bid))
        self.assertFalse(self.available(18))
        self.store.modify(self.uid, self.bid, self.target())
        with self.store.connection() as c:
            booking = c.execute('SELECT * FROM bookings WHERE id=?',(self.bid,)).fetchone()
            self.assertEqual(booking['start'],self.target()['start'])
            self.assertEqual(booking['modifications'],1)
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(self.pid,)).fetchone()[0],2)
        with self.assertRaises(Problem): self.store.modify(self.uid,self.bid,self.target(19))

    def test_new_reservations_and_batch_cannot_use_exception(self):
        with self.assertRaises(Problem): self.store.reserve(self.uid,self.target())
        with self.assertRaises(Problem): self.store.reserve_batch(self.uid,{'slots':[self.target()],'request_key':'same-day-batch-test-key','rules_acceptance':self.acceptance})

    def test_two_hour_boundary_and_different_day(self):
        self.assertFalse(self.available(10,bid=self.bid))
        self.assertTrue(self.available(11,bid=self.bid))
        with self.assertRaises(Problem): self.store.modify(self.uid,self.bid,self.target(10))
        self.clock=self.clock.replace(hour=14)
        self.assertFalse(self.available(18,bid=self.bid))
        with self.assertRaises(Problem): self.store.modify(self.uid,self.bid,self.target())

    def test_tomorrow_morning_can_be_selected_before_evening_cutoff(self):
        self.store.modify(self.uid,self.bid,self.target(7,1))
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT start FROM bookings WHERE id=?',(self.bid,)).fetchone()[0],self.target(7,1)['start'])

    def test_foreign_reservation_and_cancelled_reservation(self):
        with self.assertRaises(Problem): self.available(18,uid=self.admin,bid=self.bid)
        with self.assertRaises(Problem): self.store.modify(self.admin,self.bid,self.target())
        with self.store.connection(True) as c: c.execute("UPDATE bookings SET status='cancelled' WHERE id=?",(self.bid,))
        self.assertFalse(self.available(18,bid=self.bid))
        with self.assertRaises(Problem): self.store.modify(self.uid,self.bid,self.target())

    def test_occupied_slot_rolls_back_original(self):
        with self.store.connection(True) as c:
            c.execute("INSERT INTO bookings(user_id,package_id,box,start,created_at) VALUES(?,?,1,?,?)",(self.admin,self.pid,self.target()['start'],stamp(self.clock)))
        self.assertFalse(self.available(18,bid=self.bid))
        with self.assertRaises(Problem): self.store.modify(self.uid,self.bid,self.target())
        with self.store.connection() as c:
            b=c.execute('SELECT * FROM bookings WHERE id=?',(self.bid,)).fetchone()
            self.assertEqual((b['start'],b['modifications']),(self.start,0))


    def test_chilean_date_even_when_utc_has_changed_day(self):
        self.clock = self.clock.replace(hour=20)
        with self.store.connection(True) as c:
            c.execute('UPDATE bookings SET start=? WHERE id=?',(stamp(self.clock.replace(hour=22)),self.bid))
        self.assertTrue(self.available(22,bid=self.bid) is False)  # Original slot is occupied.
        target = dict(self.target(22),box=2)
        self.assertTrue(target['start'].startswith('2026-10-29'))
        self.store.modify(self.uid,self.bid,target)