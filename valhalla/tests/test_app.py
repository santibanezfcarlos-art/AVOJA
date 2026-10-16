import base64
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import ADMIN_EMAIL, LEGACY_DEMO_EMAILS, CHILE, Handler, Problem, Store, password_hash, stamp


class Rules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = password_hash('TestingPassword2026!')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.clock = datetime(2026, 10, 15, 9, tzinfo=CHILE)
        self.patch = patch('app.now', side_effect=lambda: self.clock)
        self.patch.start()
        with self.store.connection(True) as c:
            for uid, role, verified in [(1, 'professional', 1), (2, 'professional', 1), (3, 'professional', 0), (4, 'admin', 1)]:
                c.execute('INSERT INTO users(id,name,email,password,role,verified) VALUES(?,?,?,?,?,?)',
                          (uid, f'Persona {uid}', ADMIN_EMAIL if role=='admin' else f'{uid}@test.cl', self.encoded, role, verified))
                c.execute('UPDATE users SET email_verified=1 WHERE id=?',(uid,))
                if verified:
                    c.execute("INSERT INTO documents(id,user_id,filename,mime,content,status,created_at,kind) VALUES(?,?,?,'application/pdf',?,'approved',?,'identity')", (0 if uid==4 else -uid,uid,'fixture.pdf',b'%PDF-1.4\nTEST',stamp(self.clock)))
                if role == 'professional':
                    c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,5,5,45000,'paid',?)", (uid, stamp(self.clock)))

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def slot(self, hour=10, day=1, box=1):
        date = self.clock + timedelta(days=day)
        return {'box': box, 'start': stamp(date.replace(hour=hour, minute=0, second=0, microsecond=0))}

    def balance(self, uid=1):
        with self.store.connection() as c:
            return c.execute("SELECT SUM(remaining) n FROM packages WHERE user_id=? AND status='paid'", (uid,)).fetchone()['n']

    def row(self, bid):
        with self.store.connection() as c:
            return dict(c.execute('SELECT * FROM bookings WHERE id=?', (bid,)).fetchone())

    def test_three_boxes_share_balance(self):
        for box in (1, 2, 3):
            self.store.reserve(1, self.slot(hour=9+box, box=box))
        self.assertEqual(self.balance(), 2)

    def test_box_duplicate_rolls_back_balance(self):
        self.store.reserve(1, self.slot())
        with self.assertRaises(Problem):
            self.store.reserve(2, self.slot())
        self.assertEqual(self.balance(2), 5)

    def test_professional_cannot_double_book_across_boxes(self):
        self.store.reserve(1, self.slot())
        with self.assertRaises(Problem):
            self.store.reserve(1, self.slot(box=2))
        self.assertEqual(self.balance(), 4)

    def test_concurrent_box_requests_only_one_wins(self):
        barrier = threading.Barrier(2)
        def reserve(uid):
            barrier.wait()
            try:
                self.store.reserve(uid, self.slot())
                return True
            except Problem:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(reserve, [1, 2]))
        self.assertEqual(sum(result), 1)
        self.assertEqual(self.balance(1) + self.balance(2), 9)

    def test_concurrent_last_hour_cannot_be_overspent(self):
        with self.store.connection(True) as c:
            c.execute('UPDATE packages SET remaining=1 WHERE user_id=1')
        barrier = threading.Barrier(2)
        def reserve(hour):
            barrier.wait()
            try:
                self.store.reserve(1, self.slot(hour=hour))
                return True
            except Problem:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(reserve, [10, 11]))
        self.assertEqual(sum(result), 1)
        self.assertEqual(self.balance(), 0)

    def test_unverified_or_inactive_professional_blocked(self):
        with self.assertRaises(Problem):
            self.store.reserve(3, self.slot())
        self.store.admin_action(4, {'action':'user', 'id':1, 'active':False})
        with self.assertRaises(Problem):
            self.store.reserve(1, self.slot())

    def test_pending_package_not_spendable(self):
        with self.store.connection(True) as c:
            c.execute('UPDATE packages SET remaining=0 WHERE user_id=1')
        pid = self.store.buy(1, {'catalog_id':'5'})
        with self.assertRaises(Problem):
            self.store.reserve(1, self.slot())
        self.store.admin_action(4, {'action':'payment','id':pid,'decision':'paid'})
        self.store.reserve(1, self.slot())
        self.assertEqual(self.balance(), 4)
        with self.assertRaises(Problem):
            self.store.admin_action(4, {'action':'payment','id':pid,'decision':'paid'})
        self.assertEqual(self.balance(), 4)

    def test_package_owner_cannot_be_chosen_by_client(self):
        pid = self.store.buy(1, {'catalog_id':'10', 'user_id':2})
        with self.store.connection() as c:
            package = c.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
        self.assertEqual(package['user_id'],1)
        booking = self.store.reserve(1, {**self.slot(), 'user_id':2, 'package_id':2})
        self.assertEqual(self.row(booking)['user_id'],1)
        self.assertEqual(self.row(booking)['package_id'],1)
        self.assertEqual(self.balance(2),5)

    def test_database_rejects_package_transfer(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connection(True) as c:
                c.execute('UPDATE packages SET user_id=2 WHERE id=1')
        self.assertEqual(self.balance(1),5)
        self.assertEqual(self.balance(2),5)

    def test_repeated_access_configuration_preserves_balances(self):
        self.store.reserve(1,self.slot())
        self.store.configure_access('PrivateTestPassword2026!')
        self.store.configure_access('PrivateTestPassword2026!')
        self.assertEqual(self.balance(),4)

    def test_one_modification_preserves_balance_and_releases_slot(self):
        bid = self.store.reserve(1, self.slot())
        self.store.modify(1,bid,self.slot(11, box=2))
        self.assertEqual(self.balance(),4)
        self.assertEqual(self.row(bid)['modifications'],1)
        self.store.reserve(2,self.slot())
        with self.assertRaises(Problem):
            self.store.modify(1,bid,self.slot(12))

    def test_modify_conflict_preserves_original_and_allowance(self):
        bid = self.store.reserve(1,self.slot())
        self.store.reserve(2,self.slot(11))
        with self.assertRaises(Problem):
            self.store.modify(1,bid,self.slot(11))
        self.assertEqual(self.row(bid)['start'],self.slot()['start'])
        self.assertEqual(self.row(bid)['modifications'],0)
        self.assertEqual(self.balance(),4)

    def test_modify_noop_does_not_use_change(self):
        bid=self.store.reserve(1,self.slot())
        with self.assertRaises(Problem):
            self.store.modify(1,bid,self.slot())
        self.assertEqual(self.row(bid)['modifications'],0)

    def test_modify_enforces_cutoff_original(self):
        bid=self.store.reserve(1,self.slot(10,day=0))
        with self.assertRaises(Problem):
            self.store.modify(1,bid,self.slot(12,day=0))

    def test_modify_enforces_cutoff_target(self):
        bid=self.store.reserve(1,self.slot())
        with self.assertRaises(Problem):
            self.store.modify(1,bid,self.slot(10,day=0))

    def test_exact_two_hour_boundary_is_allowed(self):
        bid=self.store.reserve(1,self.slot(11,day=0))
        self.store.modify(1,bid,self.slot(12,day=0))
        self.assertEqual(self.row(bid)['modifications'],1)

    def test_other_professional_cannot_modify(self):
        bid=self.store.reserve(1,self.slot())
        with self.assertRaises(Problem):
            self.store.modify(2,bid,self.slot(11))

    def test_cancel_refunds_original_package_once(self):
        bid=self.store.reserve(1,self.slot())
        self.store.admin_action(4,{'action':'cancel','id':bid})
        self.assertEqual(self.balance(),5)
        with self.assertRaises(Problem):
            self.store.admin_action(4,{'action':'cancel','id':bid})
        self.assertEqual(self.balance(),5)
        self.store.reserve(2,self.slot())

    def test_invalid_past_partial_outside_or_distant_slots(self):
        slots=[self.slot(7), self.slot(20), self.slot(8,day=0), self.slot(day=91)]
        slots.append({'box':1,'start':stamp((self.clock+timedelta(days=1)).replace(minute=30))})
        slots.append({'box':1,'start':'2026-10-16T10:00:00'})
        for slot in slots:
            with self.subTest(slot=slot), self.assertRaises(Problem):
                self.store.reserve(1,slot)

    def test_equivalent_offset_does_not_bypass_duplicate(self):
        slot=self.slot()
        self.store.reserve(1,slot)
        local=dict(slot,start=datetime.fromisoformat(slot['start']).astimezone(CHILE).isoformat())
        with self.assertRaises(Problem):
            self.store.reserve(2,local)

    def test_availability_exposes_slots_not_user_data(self):
        slot=self.slot()
        self.store.reserve(1,slot)
        data=self.store.availability((self.clock+timedelta(days=1)).date().isoformat())
        self.assertEqual(len(data['slots']),12)
        row=next(s for s in data['slots'] if s['start']==slot['start'])
        self.assertFalse(row['boxes'][0]['available'])
        self.assertTrue(row['boxes'][1]['available'])
        self.assertNotIn('user_id',json.dumps(data))

    def test_document_review_and_reupload(self):
        pdf={'kind':'identity','filename':'demo.pdf','content':base64.b64encode(b'%PDF-1.4\nFICTICIO').decode()}
        self.store.upload(3,pdf)
        self.store.admin_action(4,{'action':'document','id':1,'decision':'approved','identity_confirmed':True})
        self.store.reserve(3,self.slot())
        self.store.upload(3,pdf)
        with self.assertRaises(Problem):
            self.store.reserve(3,self.slot(11))
        with self.assertRaises(Problem):
            self.store.admin_action(4,{'action':'document','id':1,'decision':'approved','identity_confirmed':True})
        with self.assertRaises(Problem):
            self.store.admin_action(4,{'action':'document','id':2,'decision':'rejected'})
        self.store.admin_action(4,{'action':'document','id':2,'decision':'rejected','note':'Documento incompleto'})

    def test_invalid_or_empty_files_are_rejected(self):
        for data in [{'filename':'x.pdf','content':'!!'},
                     {'filename':'empty.png','content':''},
                     {'filename':'large.bin','content':base64.b64encode(b'x'*(10*1024*1024+1)).decode()}]:
            with self.subTest(data=data['filename']),self.assertRaises(Problem):
                self.store.upload(1,data)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.store=Store(Path(cls.temp.name)/'http.sqlite3')
        cls.store.configure_access('PrivateAdminTest2026!')
        cls.store.register({'name':'Profesional Prueba','email':'professional@test.cl','password':'ProfessionalTest2026!','specialty':'Psicología'})
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.server.store=cls.store
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,body=None,cookie='',csrf='',origin=None):
        headers={'Content-Type':'application/json','Cookie':cookie,'X-CSRF-Token':csrf}
        if origin:headers['Origin']=origin
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:response=urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:response=e
        raw=response.read()
        data=json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else raw
        return response.status,data,response.headers

    def login(self,admin=False):
        status,data,headers=self.request('/api/login',{'email':ADMIN_EMAIL if admin else 'professional@test.cl',
                                               'password':'PrivateAdminTest2026!' if admin else 'ProfessionalTest2026!'})
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0]
        _,profile,_=self.request('/api/me',cookie=cookie)
        return cookie,profile['csrf']

    def test_auth_admin_and_csrf_permissions(self):
        self.assertEqual(self.request('/api/me')[0],401)
        cookie,csrf=self.login()
        self.assertEqual(self.request('/api/admin',cookie=cookie)[0],403)
        self.assertEqual(self.request('/api/admin',{'action':'cancel','id':1},cookie,csrf)[0],403)
        self.assertEqual(self.request('/api/packages',{'catalog_id':'5'},cookie)[0],403)
        self.assertEqual(self.request('/api/packages',{'catalog_id':'5'},cookie,csrf,origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/api/packages',{'catalog_id':'5'},cookie,csrf)[0],200)
        admin,admincsrf=self.login(True)
        status,data,_=self.request('/api/admin',cookie=admin)
        self.assertEqual(status,200)
        self.assertNotIn('password',json.dumps(data))

    def test_logout_invalidates_session(self):
        cookie,csrf=self.login()
        self.assertEqual(self.request('/api/logout',{},cookie,csrf)[0],200)
        self.assertEqual(self.request('/api/me',cookie=cookie)[0],401)

    def test_document_download_access_and_attachment(self):
        cookie,csrf=self.login()
        self.request('/api/documents',{'filename':'test.pdf','content':base64.b64encode(b'%PDF-1.4\nFAKE').decode()},cookie,csrf)
        _,me,_=self.request('/api/me',cookie=cookie)
        did=me['documents'][0]['id']
        status,body,headers=self.request(f'/api/documents/{did}',cookie=cookie)
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(self.request(f'/api/documents/{did}')[0],401)
        token=self.store.login({'email':ADMIN_EMAIL,'password':'PrivateAdminTest2026!'})
        self.assertEqual(self.request(f'/api/documents/{did}',cookie='session='+token)[0],200)
        self.store.register({'name':'Otra persona','email':'otro@ejemplo.cl','password':'OtraPersona2026!','specialty':'Kinesiología'})
        other=self.store.login({'email':'otro@ejemplo.cl','password':'OtraPersona2026!'})
        self.assertEqual(self.request(f'/api/documents/{did}',cookie='session='+other)[0],404)

    def test_registration_cookie_and_static_page(self):
        status,body,headers=self.request('/api/register',{'name':'Registro prueba','email':'nuevo@ejemplo.cl','password':'NuevaPersona2026!','password_confirmation':'NuevaPersona2026!','specialty':'Psicología','role':'admin','verified':True})
        self.assertEqual(status,200)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        cookie=headers['Set-Cookie'].split(';')[0]
        _,profile,_=self.request('/api/me',cookie=cookie)
        self.assertEqual(profile['user']['role'],'professional')
        self.assertEqual(profile['user']['verified'],0)
        self.assertEqual(self.request('/api/register',{'name':'Registro prueba','email':'nuevo@ejemplo.cl','password':'NuevaPersona2026!','password_confirmation':'NuevaPersona2026!','specialty':'Psicología'})[0],409)
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/../../app.py')[0],404)

    def test_registration_requires_matching_confirmation(self):
        body={'name':'Confirmation Test','email':'confirmation@example.cl','password':'NewPassword2026!','specialty':'Psicología'}
        self.assertEqual(self.request('/api/register',body)[0],400)
        self.assertEqual(self.request('/api/register',dict(body,password_confirmation='WrongPassword2026!'))[0],400)
        with self.store.connection() as c:
            self.assertIsNone(c.execute('SELECT id FROM users WHERE email=?',(body['email'],)).fetchone())

    def test_recovery_without_mail_service_fails_without_tokens(self):
        self.assertEqual(self.request('/api/password-reset/request',{'email':'professional@test.cl'})[0],503)

    def test_recovery_same_response_for_unknown_and_existing_and_no_token_leak(self):
        from unittest.mock import Mock
        delivery=Mock(configured=True, spec=['configured','send_password_reset'])
        self.server.email_delivery=delivery
        try:
            known=self.request('/api/password-reset/request',{'email':'professional@test.cl'})
            unknown=self.request('/api/password-reset/request',{'email':'unknown-reset@test.cl'})
            self.assertEqual(known[0],200)
            self.assertEqual(known[:2],unknown[:2])
            token=delivery.send_password_reset.call_args.args[1]
            self.assertNotIn(token,json.dumps(known[1]))
            self.assertNotIn('Set-Cookie',known[2])
            blocked=self.request('/api/password-reset/request',{'email':'another-reset@test.cl'},origin='https://other.example')
            self.assertEqual(blocked[0],403)
        finally:
            del self.server.email_delivery


if __name__=='__main__':
    unittest.main()
