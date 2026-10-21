import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from app import Store,Problem,ADMIN_EMAIL,CHILE,stamp

class BatchBookings(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'batch.sqlite3');self.store.configure_access('BatchAdminTest2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'BatchAdminTest2026!'}))['id']
        self.clock=datetime(2026,10,19,8,tzinfo=CHILE)
        self.clock_patch=patch('app.now',return_value=self.clock);self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        self.user=self.client('one@example.cl',5)
    def client(self,email,hours):
        uid=self.store.register({'name':'Profesional '+email,'email':email,'specialty':'Psicología','password':'BatchProTest2026!'})
        token,_=self.store.issue_email_token(uid);self.store.confirm_email(token)
        self.store.import_legacy(self.admin,{'name':'Profesional '+email,'email':email,'specialty':'Psicología','sessions':hours,'reference':'legacy-'+email,'note':'Validación y saldo anteriores','identity_confirmed':True})
        return uid
    def slot(self,hour=10,box=1,day=1):return {'start':stamp((self.clock+timedelta(days=day)).replace(hour=hour)),'box':box}
    def balance(self,uid=None):
        with self.store.connection() as c:return c.execute("SELECT SUM(remaining) FROM packages WHERE user_id=? AND status='paid'",(uid or self.user,)).fetchone()[0]
    def batch(self,slots,key='test-batch-key-123456789',uid=None):return self.store.reserve_batch(uid or self.user,{'slots':slots,'request_key':key})
    def count(self,table,where=''):
        with self.store.connection() as c:return c.execute('SELECT COUNT(*) FROM '+table+' '+where).fetchone()[0]
    def prepare_owner(self):
        with self.store.connection(True) as c:
            for kind in ('identity','degree'):
                c.execute("INSERT INTO documents(user_id,filename,mime,content,status,created_at,kind) VALUES(?,?,'application/pdf',?,'approved',?,?)",(self.admin,kind+'.pdf',b'Fixture',stamp(self.clock),kind))
            c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,6,6,0,'paid',?)",(self.admin,stamp(self.clock)))
    def test_owner_can_reserve_three_boxes_same_time_individually(self):
        self.prepare_owner()
        for box in (1,2,3):self.store.reserve(self.admin,self.slot(9,box))
        self.assertEqual(self.balance(self.admin),3)
        with self.assertRaises(Problem):self.store.reserve(self.admin,self.slot(9,1))
        with self.assertRaises(Problem):self.store.reserve(self.user,self.slot(9,2))
        self.assertEqual(self.balance(self.admin),3);self.assertEqual(self.balance(),5)
    def test_owner_parallel_batch_idempotence_and_occupied_box_rollback(self):
        self.prepare_owner();slots=[self.slot(9,box) for box in (1,2,3)]
        first=self.batch(slots,uid=self.admin);second=self.batch(slots,uid=self.admin)
        self.assertEqual(first['ids'],second['ids']);self.assertEqual(self.balance(self.admin),3)
        with self.assertRaises(Problem):self.batch([self.slot(10,1),self.slot(9,2)],key='occupied-owner-key-12345',uid=self.admin)
        self.assertEqual(self.balance(self.admin),3);self.assertEqual(self.count('bookings'),3)
        with self.assertRaises(Problem):self.batch([self.slot(11,1),self.slot(11,1)],key='duplicate-owner-key-1234',uid=self.admin)
    def test_owner_modification_to_parallel_time_but_not_occupied_box(self):
        self.prepare_owner();self.store.reserve(self.admin,self.slot(9,1))
        bid=self.store.reserve(self.admin,self.slot(10,2))
        with self.assertRaises(Problem):self.store.modify(self.admin,bid,self.slot(9,1))
        self.store.modify(self.admin,bid,self.slot(9,3))
        self.assertEqual(self.balance(self.admin),4)
    def test_professional_still_cannot_reserve_simultaneously_or_modify_to_same_time(self):
        self.store.reserve(self.user,self.slot(9,1))
        with self.assertRaises(Problem):self.store.reserve(self.user,self.slot(9,2)|{'role':'admin'})
        bid=self.store.reserve(self.user,self.slot(10,2))
        with self.assertRaises(Problem):self.store.modify(self.user,bid,self.slot(9,3))
        self.assertEqual(self.balance(),3)
    def test_all_five_across_days_and_boxes_with_individual_receipts(self):
        result=self.batch([self.slot(hour=hour,box=(hour%3)+1,day=hour%2+1) for hour in range(7,12)])
        self.assertEqual(len(result['ids']),5);self.assertEqual(self.balance(),0)
        self.assertEqual(self.count('notification_outbox',"WHERE kind='booking_confirmed'"),5)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT SUM(modifications) FROM bookings').fetchone()[0],0)
    def test_one_slot_under_24_hours_rejects_entire_batch_without_balance_or_receipts(self):
        before=self.count('notification_outbox')
        with self.assertRaises(Problem) as error:self.batch([self.slot(12,day=2),self.slot(7,day=1)])
        self.assertIn('24 horas',error.exception.message)
        self.assertEqual(self.balance(),5);self.assertEqual(self.count('bookings'),0)
        self.assertEqual(self.count('notification_outbox'),before)

    def test_partial_booking_leaves_remaining_for_later(self):
        self.batch([self.slot(10),self.slot(11)])
        self.assertEqual(self.balance(),3)
        with self.store.connection() as c:
            balances=[json.loads(row['payload'])['remaining'] for row in c.execute("SELECT payload FROM notification_outbox WHERE kind='booking_confirmed'")]
        self.assertEqual(balances,[3,3])
        self.batch([self.slot(22,box=3,day=5)],key='second-batch-key-123456789')
        self.assertEqual(self.balance(),2)
    def test_batch_uses_oldest_paid_plan_then_next_without_touching_other_users(self):
        with self.store.connection(True) as c:
            first=c.execute('SELECT id FROM packages WHERE user_id=?',(self.user,)).fetchone()[0]
            second=c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,2,2,100,'paid',?)",(self.user,stamp(self.clock))).lastrowid
        self.batch([self.slot(hour,day=2) for hour in range(7,13)])
        self.assertEqual(self.balance(),1)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bookings WHERE package_id=?',(first,)).fetchone()[0],5)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bookings WHERE package_id=?',(second,)).fetchone()[0],1)

    def test_conflict_rolls_back_all_bookings_balances_and_emails(self):
        other=self.client('other@example.cl',5);self.store.reserve(other,self.slot(11))
        before=self.count('notification_outbox')
        with self.assertRaises(Problem):self.batch([self.slot(10),self.slot(11)])
        self.assertEqual(self.balance(),5);self.assertEqual(self.count('bookings'),1)
        self.assertEqual(self.count('notification_outbox'),before);self.assertEqual(self.count('booking_batches'),0)
    def test_invalid_excess_balance_duplicate_times_and_out_of_hours_make_no_changes(self):
        for slots in ([],[self.slot(hour,day=2) for hour in range(7,13)],[self.slot(10),self.slot(10,box=2)],[self.slot(10),self.slot(23)],[self.slot(10),dict(self.slot(11),box=True)]):
            with self.assertRaises(Problem):self.batch(slots)
        self.assertEqual(self.balance(),5);self.assertEqual(self.count('bookings'),0)
    def test_idempotent_retry_does_not_charge_or_send_again_and_changed_selection_rejected(self):
        slots=[self.slot(10),self.slot(11)]
        first=self.batch(slots);second=self.batch(list(reversed(slots)))
        self.assertEqual(first['ids'],second['ids']);self.assertTrue(second['repeated']);self.assertEqual(self.balance(),3)
        self.assertEqual(self.count('notification_outbox',"WHERE kind='booking_confirmed'"),2)
        with self.assertRaises(Problem):self.batch([self.slot(12)])
    def test_two_professionals_competing_for_one_batch_only_one_wins(self):
        other=self.client('other@example.cl',5)
        def book(uid):
            try:self.batch([self.slot(10),self.slot(11)],uid=uid);return True
            except Problem:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(book,[self.user,other]))
        self.assertEqual(sum(results),1);self.assertEqual(self.count('bookings'),2)
        self.assertEqual(self.balance()+self.balance(other),8)
    def test_two_batches_cannot_overspend_one_users_last_sessions(self):
        def book(hour):
            try:self.batch([self.slot(hour),self.slot(hour+1),self.slot(hour+2)],key=f'parallel-batch-{hour}-123456789');return True
            except Problem:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(book,[7,14]))
        self.assertEqual(sum(results),1);self.assertEqual(self.balance(),2);self.assertEqual(self.count('bookings'),3)
    def test_unapproved_professional_cannot_bypass_validation(self):
        with self.store.connection(True) as c:c.execute('UPDATE legacy_imports SET identity_valid=0 WHERE user_id=?',(self.user,))
        with self.assertRaises(Problem):self.batch([self.slot(10)])
        self.assertEqual(self.balance(),5)
    def test_http_batch_requires_session_and_csrf_and_ignores_forged_owner(self):
        import threading,http.client
        from http.server import ThreadingHTTPServer
        from app import Handler
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        token=self.store.login({'email':'one@example.cl','password':'BatchProTest2026!'});csrf=self.store.session(token)['csrf']
        def request(token='',csrf=''):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('POST','/api/bookings/batch',json.dumps({'slots':[self.slot(10),self.slot(11)],'request_key':'http-batch-key-123456789','user_id':self.admin}),{'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf})
            response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            self.assertEqual(request()[0],401);self.assertEqual(request(token)[0],403)
            self.assertEqual(request(token,csrf)[0],200)
            with self.store.connection() as c:self.assertEqual({row[0] for row in c.execute('SELECT user_id FROM bookings')},{self.user})
        finally:server.shutdown();server.server_close();thread.join()
