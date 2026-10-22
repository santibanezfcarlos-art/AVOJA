"""Owner-delegated office access: scoped reads and audited hour credits, no real email."""
import json
import secrets
import tempfile
import unittest
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
import http.client
from app import Store,Handler,Problem,ADMIN_EMAIL,DEFAULT_MANAGEMENT_EMAIL


class ManagementAccess(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'management.sqlite3');self.password='SyntheticOwnerPassword2026!';self.store.configure_access(self.password)
        self.owner_token=self.store.login({'email':ADMIN_EMAIL,'password':self.password});self.owner=self.store.session(self.owner_token)['id']
        self.manager_credentials={'name':'Gestora Oficina','email':DEFAULT_MANAGEMENT_EMAIL,'specialty':'Psicología','password':'SyntheticManager2026!'}
        self.manager=self.store.register(self.manager_credentials)
        token,_=self.store.issue_email_token(self.manager);self.store.confirm_email(token)
        self.manager_token=self.store.login(self.manager_credentials)
        self.other=self.store.register({'name':'Otro Profesional','email':'another@example.cl','specialty':'Psicología','password':'SyntheticOther2026!'})
        self.body={'user_id':self.manager,'hours':2,'method':'courtesy','amount':0,'confirmed':True,'request_key':'management-test-credit-123456'}
    def test_verified_designated_email_gets_management_without_owner_booking_privileges(self):
        state=self.store.state(self.store.session(self.manager_token))
        self.assertTrue(state['user']['management_access']);self.assertEqual(state['user']['role'],'professional')
        self.assertFalse(state['user']['owner_booking_privileges']);self.assertFalse(state['user']['verified'])
        token=self.store.login(self.manager_credentials|{'portal':'admin'});self.assertEqual(self.store.session(token)['id'],self.manager)
        with self.store.connection() as c:self.assertFalse(self.store.management_access(c,self.other))
    def test_can_credit_self_and_others_idempotently_with_responsible_person_recorded(self):
        first=self.store.manual_credit(self.manager,self.body)
        self.assertTrue(self.store.manual_credit(self.manager,self.body)['repeated'])
        second=self.store.manual_credit(self.manager,self.body|{'user_id':self.other,'hours':3,'method':'transfer','amount':15000,'reference':'PAYMENT-SYNTHETIC-001','request_key':'management-other-credit-123456'})
        with self.store.connection() as c:
            credits=c.execute('SELECT actor_id,user_id FROM manual_credits ORDER BY id').fetchall()
            self.assertEqual([(r['actor_id'],r['user_id']) for r in credits],[(self.manager,self.manager),(self.manager,self.other)])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_outbox WHERE kind='hours_credited'").fetchone()[0],2)
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(first['package_id'],)).fetchone()[0],2)
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(second['package_id'],)).fetchone()[0],3)
    def test_management_payload_excludes_documents_mail_config_imports_and_owner_controls(self):
        self.store.manual_credit(self.manager,self.body)
        self.store.save_discount(self.owner,{'code':'PRIVATEOWNER','percent':15})
        view=self.store.admin_state(self.manager)
        self.assertEqual(len(view['manual_credits']),1);self.assertTrue(view['users']);self.assertTrue(view['packages'])
        for key in ('documents','discount_codes','personal_approvals','migrations','notification_queue','mail_events','blocked_emails','user_access_events','audit'):
            self.assertEqual(view[key],[])
        with self.assertRaises(Problem):self.store.admin_state(self.other)
    def test_revocation_is_immediate_and_persists_across_access_reconfiguration(self):
        self.store.set_management_access(self.owner,{'user_id':self.manager,'enabled':False,'confirmed':True})
        self.assertFalse(self.store.state(self.store.session(self.manager_token))['user']['management_access'])
        with self.assertRaises(Problem):self.store.manual_credit(self.manager,self.body)
        with self.assertRaises(Problem):self.store.login(self.manager_credentials|{'portal':'admin'})
        self.store.configure_access(self.password)
        with self.store.connection() as c:self.assertFalse(self.store.management_access(c,self.manager))
        self.assertEqual(self.store.session(self.store.login(self.manager_credentials))['id'],self.manager)
    def test_management_cannot_grant_permissions_or_adjust_other_balances(self):
        for actor in (self.manager,self.other):
            with self.assertRaises(Problem):self.store.set_management_access(actor,{'user_id':actor,'enabled':True,'confirmed':True})
        self.store.update_profile(self.other,{'name':'Otro Profesional','specialty':'Psicología','role':'admin','management_access':True})
        with self.store.connection() as c:self.assertFalse(self.store.management_access(c,self.other))
        with self.assertRaises(Problem):self.store.manual_credit(self.other,self.body)
    def test_unverified_email_is_not_sufficient_to_claim_management(self):
        with self.store.connection(True) as c:c.execute('UPDATE users SET email_verified=0 WHERE id=?',(self.manager,))
        with self.store.connection() as c:self.assertFalse(self.store.management_access(c,self.manager))
        with self.assertRaises(Problem):self.store.manual_credit(self.manager,self.body)
    def test_blocking_and_deletion_disable_management(self):
        self.store.manage_user_access(self.owner,{'user_id':self.manager,'action':'block','confirmed':True})
        with self.store.connection() as c:self.assertFalse(self.store.management_access(c,self.manager))
        self.store.manage_user_access(self.owner,{'email':DEFAULT_MANAGEMENT_EMAIL,'action':'unblock','confirmed':True})
        self.store.manage_user_access(self.owner,{'user_id':self.manager,'action':'delete','confirmed':True})
        with self.store.connection() as c:
            self.assertFalse(self.store.management_access(c,self.manager))
            self.assertEqual(c.execute('SELECT active FROM management_grants WHERE email=?',(DEFAULT_MANAGEMENT_EMAIL,)).fetchone()[0],0)
    def test_http_scopes_and_csrf_are_enforced_even_with_a_professional_session(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,token=None,csrf=True):
            token=token or self.manager_token
            headers={'Content-Type':'application/json','Cookie':'session='+token}
            if csrf:headers['X-CSRF-Token']=self.store.session(token)['csrf']
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers=headers)
            response=connection.getresponse();raw=response.read();result=(response.status,json.loads(raw));connection.close();return result
        try:
            with self.store.connection(True) as c:
                did=c.execute("INSERT INTO documents(user_id,filename,mime,content,status,created_at) VALUES(?,'private-proof.pdf','application/pdf',?,'approved','2026-10-22')",(self.other,b'%PDF synthetic private document')).lastrowid
            status,data=request('/api/admin');self.assertEqual(status,200);self.assertNotIn('email_config',data);self.assertNotIn('webpay_config',data)
            self.assertEqual(request('/api/documents/'+str(did))[0],404)
            self.assertEqual(request('/api/admin/availability?date=2026-10-23')[0],200)
            self.assertEqual(request('/api/admin/manual-credits',self.body)[0],200)
            self.assertEqual(request('/api/admin/manual-credits',self.body,csrf=False)[0],403)
            for path,body in [('/api/admin/management-access',{'user_id':self.manager,'enabled':True,'confirmed':True}),('/api/admin/users/access',{'user_id':self.other,'action':'delete','confirmed':True}),('/api/admin/session-adjustments',{}),('/api/admin/packages/archive',{}),('/api/admin/discounts',{}),('/api/admin/webpay/live-sales',{}),('/api/admin',{'action':'user','id':self.other,'active':False}),('/api/admin/daily-contacts/skedu/import',{})]:
                self.assertEqual(request(path,body)[0],403,path)
            self.assertEqual(request('/api/admin/daily-contacts?date=2026-10-23')[0],403)
            other_token=self.store.login({'email':'another@example.cl','password':'SyntheticOther2026!'})
            self.assertEqual(request('/api/admin',token=other_token)[0],403)
            self.store.set_management_access(self.owner,{'user_id':self.manager,'enabled':False,'confirmed':True})
            self.assertEqual(request('/api/admin')[0],403)
        finally:server.shutdown();server.server_close();thread.join()
