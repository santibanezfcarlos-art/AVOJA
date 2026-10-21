import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime,timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from app import Store,Handler,Problem,ADMIN_EMAIL,now,stamp,CHILE
from rules import RULES_VERSION,RULES_TEXT

class BookingRules(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'rules.sqlite3');self.store.configure_access('BookingRulesAdmin2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'BookingRulesAdmin2026!'}))['id']
        credentials={'name':'Colega Cortesía','email':'courtesy-rules@example.cl','specialty':'Psicología','password':'CourtesyRules2026!'}
        self.uid=self.store.register(credentials);token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.token=self.store.login(credentials);self.csrf=self.store.session(self.token)['csrf']
        self.store.admin_action(self.admin,{'action':'personal_approval','id':self.uid,'decision':'approved','confirmed':True})
        self.pid=self.store.manual_credit(self.admin,{'user_id':self.uid,'hours':3,'amount':0,'method':'courtesy','confirmed':True,'request_key':'courtesy-rules-key-123456'})['package_id']
        self.acceptance={'accepted':True,'version':RULES_VERSION}
        self.start=stamp((now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0))
    def slot(self,box=1):return {'box':box,'start':self.start,'rules_acceptance':self.acceptance}
    def counts(self):
        with self.store.connection() as c:return tuple(c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('bookings','booking_rule_acceptances','purchase_acceptances'))
    def test_courtesy_accepts_rules_atomically_at_booking_without_separate_plan_step(self):
        bid=self.store.reserve(self.uid,self.slot())
        with self.store.connection() as c:
            row=c.execute('SELECT * FROM booking_rule_acceptances WHERE booking_id=?',(bid,)).fetchone()
            self.assertEqual((row['user_id'],row['version'],row['rules_text']),(self.uid,RULES_VERSION,RULES_TEXT))
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(self.pid,)).fetchone()[0],2)
        self.assertEqual(self.counts(),(1,1,1))
        self.store.reserve(self.uid,dict(self.slot(),start=stamp(datetime.fromisoformat(self.start)+timedelta(hours=1))))
        self.assertEqual(self.counts(),(2,2,1))
    def test_batch_records_acceptance_per_reservation_and_retry_does_not_duplicate(self):
        second=dict(self.slot(),start=stamp(datetime.fromisoformat(self.start)+timedelta(hours=1)))
        body={'slots':[self.slot(),second],'request_key':'booking-rules-batch-123456','rules_acceptance':self.acceptance}
        first=self.store.reserve_batch(self.uid,body);self.store.reserve_batch(self.uid,body)
        self.assertEqual(len(first['ids']),2);self.assertEqual(self.counts(),(2,2,1))
    def test_failed_batch_rolls_back_acceptance_and_balance(self):
        occupied=stamp(datetime.fromisoformat(self.start)+timedelta(hours=1))
        with self.store.connection(True) as c:
            pid=c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,1,0,0,'paid',?)",(self.admin,stamp(now()))).lastrowid
            c.execute('INSERT INTO bookings(user_id,package_id,box,start,created_at) VALUES(?,?,1,?,?)',(self.admin,pid,occupied,stamp(now())))
        with self.assertRaises(Problem):self.store.reserve_batch(self.uid,{'slots':[self.slot(),{'box':1,'start':occupied}],'request_key':'booking-rules-rollback-123','rules_acceptance':self.acceptance})
        self.assertEqual(self.counts(),(1,0,0))
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(self.pid,)).fetchone()[0],3)
    def test_api_requires_current_acceptance_for_each_booking_and_modification(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def post(path,body):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port)
            c.request('POST',path,json.dumps(body),{'Content-Type':'application/json','Cookie':'session='+self.token,'X-CSRF-Token':self.csrf})
            r=c.getresponse();status=r.status;r.read();c.close();return status
        for acceptance in (None,{'accepted':False,'version':RULES_VERSION},{'accepted':True,'version':'old'}):
            for path,body in [('/api/bookings',self.slot()),('/api/bookings/batch',{'slots':[self.slot()],'request_key':'booking-api-rules-12345'}),('/api/bookings/1',self.slot())]:
                with self.subTest(path=path,acceptance=acceptance):self.assertEqual(post(path,body|{'rules_acceptance':acceptance}),400)
        self.assertEqual(self.counts(),(0,0,0));self.assertEqual(post('/api/bookings',self.slot()),200)
        self.assertEqual(post('/api/bookings/1',self.slot(2)),200);self.assertEqual(self.counts(),(1,2,1))
