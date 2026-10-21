import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from app import Store,Problem,ADMIN_EMAIL,CHILE,stamp
from rules import RULES_VERSION

class OwnerPrivileges(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'owner.sqlite3');self.store.configure_access('OwnerPermissions2026!')
        self.token=self.store.login({'email':ADMIN_EMAIL,'password':'OwnerPermissions2026!'});self.owner=self.store.session(self.token)['id']
        self.clock=datetime(2026,10,21,9,45,tzinfo=CHILE);self.clock_patch=patch('app.now',return_value=self.clock);self.mock_clock=self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        self.rules={'accepted':True,'version':RULES_VERSION}
        self.uid=self.store.register({'name':'Profesional Normal','email':'normal-owner-check@example.cl','specialty':'Psicología','password':'NormalPermissions2026!'})
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.store.admin_action(self.owner,{'action':'personal_approval','id':self.uid,'decision':'approved','confirmed':True})
        for uid in (self.uid,self.owner):
            self.store.manual_credit(self.owner,{'user_id':uid,'hours':6,'amount':0,'method':'courtesy','confirmed':True,'request_key':'owner-permissions-credit-'+str(uid)})
    def slot(self,hour=10,box=1,day=0):return {'start':stamp((self.clock+timedelta(days=day)).replace(hour=hour,minute=0)),'box':box,'rules_acceptance':self.rules}
    def state(self):return self.store.state(self.store.session(self.token))
    def test_owner_needs_no_documents_and_can_reserve_fifteen_minutes_ahead(self):
        state=self.state();self.assertEqual(state['user']['verified'],1);self.assertEqual(state['user']['identity_source'],'administrator');self.assertTrue(state['user']['owner_booking_privileges']);self.assertEqual(state['documents'],[]);self.assertEqual(state['earliest_booking_date'],'2026-10-21')
        self.store.reserve(self.owner,self.slot());self.assertEqual(self.state()['packages'][0]['remaining'],5)
        self.store.update_profile(self.owner,{'name':'Administrador Renombrado','specialty':'Psicología'})
        self.assertTrue(self.state()['user']['verified'])
    def test_owner_same_day_batch_three_rooms_and_double_booking_protection(self):
        body={'slots':[self.slot(box=box) for box in (1,2,3)],'rules_acceptance':self.rules,'request_key':'owner-same-day-batch-12345'}
        result=self.store.reserve_batch(self.owner,body);self.assertEqual(len(result['ids']),3);self.assertEqual(self.state()['packages'][0]['remaining'],3)
        with self.assertRaises(Problem):self.store.reserve(self.owner,self.slot(box=1))
        with self.assertRaises(Problem):self.store.reserve(self.owner,self.slot(hour=9))
        with self.assertRaises(Problem):self.store.reserve(self.owner,self.slot(hour=23))
        self.assertEqual(self.state()['packages'][0]['remaining'],3)
    def test_owner_changes_multiple_times_under_two_hours_but_not_after_start(self):
        bid=self.store.reserve(self.owner,self.slot())
        self.store.modify(self.owner,bid,self.slot(box=2));self.store.modify(self.owner,bid,self.slot(hour=11,box=3))
        self.assertEqual(self.state()['bookings'][0]['modifications'],2);self.assertEqual(self.state()['packages'][0]['remaining'],5)
        self.mock_clock.return_value=self.clock.replace(hour=11,minute=30)
        with self.assertRaises(Problem):self.store.modify(self.owner,bid,self.slot(hour=12))
    def test_professional_cannot_gain_owner_rights_with_request_fields(self):
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot()|{'owner_booking_privileges':True,'role':'admin','user_id':self.owner})
        day=self.store.availability('2026-10-21',uid=self.uid);self.assertFalse(any(box['available'] for slot in day['slots'] for box in slot['boxes']))
        with self.store.connection(True) as c:c.execute('UPDATE users SET active=0 WHERE id=?',(self.owner,))
        with self.assertRaises(Problem):self.store.reserve(self.owner,self.slot())
    def test_owner_availability_matches_same_day_server_permission_and_preserves_occupation(self):
        self.store.reserve(self.owner,self.slot())
        grid=self.store.availability('2026-10-21',uid=self.owner)
        slot=next(s for s in grid['slots'] if s['label']=='10:00');self.assertEqual([b['available'] for b in slot['boxes']],[False,True,True])
        earlier=next(s for s in grid['slots'] if s['label']=='09:00');self.assertFalse(any(b['available'] for b in earlier['boxes']))
    def test_owner_still_requires_balance_and_cannot_reserve_inactive_account(self):
        with self.store.connection(True) as c:c.execute('UPDATE packages SET remaining=0 WHERE user_id=?',(self.owner,))
        with self.assertRaises(Problem):self.store.reserve(self.owner,self.slot())
