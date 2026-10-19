import base64
import json
import tempfile
import unittest
from pathlib import Path
from app import Store, Problem, ADMIN_EMAIL
from payments import PaymentService, PaymentError
from rules import RULES, RULES_TEXT, RULES_VERSION, validate_acceptance
from test_migration_payments import FakeGateway

ACCEPT={'accepted':True,'version':RULES_VERSION}

class RulesAndDegree(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'rules.sqlite3');self.store.configure_access('AdminRulesTest2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'AdminRulesTest2026!'}))['id']
        self.credentials={'name':'Profesional Documentos','email':'documents@example.cl','specialty':'Psicología','password':'RulesPassword2026!'}
        self.uid=self.store.register(self.credentials)
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.gateway=FakeGateway();self.payments=PaymentService(self.store,self.gateway)
    def profile(self):return self.store.state(self.store.session(self.store.login(self.credentials)))['user']
    def upload(self,kind):
        self.store.upload(self.uid,{'kind':kind,'filename':kind+'.pdf','content':base64.b64encode(b'Fictitious document').decode()})
        with self.store.connection() as c:return c.execute('SELECT MAX(id) FROM documents WHERE user_id=?',(self.uid,)).fetchone()[0]
    def approve(self,did):self.store.admin_action(self.admin,{'action':'document','id':did,'decision':'approved','identity_confirmed':True})
    def test_rules_acceptance_required_before_simulated_or_bank_purchase_no_side_effects(self):
        for invalid in (None,{}, {'accepted':False,'version':RULES_VERSION},{'accepted':'true','version':RULES_VERSION},{'accepted':True,'version':'old'}):
            with self.assertRaises(Problem):self.store.buy(self.uid,{'catalog_id':'1','rules_acceptance':invalid})
            with self.assertRaises(PaymentError):self.payments.create(self.uid,'1','rules-test-123456789',invalid)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM packages').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM purchase_acceptances').fetchone()[0],0)
        self.assertEqual(self.gateway.create_calls,0)
    def test_acceptance_snapshot_linked_to_owner_and_plan_and_bank_retry_not_duplicated(self):
        pid=self.store.buy(self.uid,{'catalog_id':'1','rules_acceptance':ACCEPT})
        checkout=self.payments.create(self.uid,'5','rules-valid-123456789',ACCEPT)
        self.assertEqual(self.payments.create(self.uid,'5','rules-valid-123456789',ACCEPT),checkout)
        with self.store.connection() as c:
            rows=c.execute('SELECT * FROM purchase_acceptances').fetchall()
            self.assertEqual(len(rows),2)
            for row in rows:
                self.assertEqual(row['user_id'],self.uid);self.assertEqual(row['version'],RULES_VERSION)
                self.assertEqual(row['rules_text'],RULES_TEXT);self.assertTrue(row['accepted_at'])
        self.assertEqual(len(RULES),12)
        self.assertIn('valhallagartenoffice@gmail.com',RULES_TEXT)
        self.assertEqual(self.store.admin_state()['packages'][0]['rules_version'],RULES_VERSION)
    def test_both_documents_required_and_replacing_degree_blocks_until_review(self):
        identity=self.upload('identity');self.approve(identity)
        self.assertTrue(self.profile()['identity_approved']);self.assertFalse(self.profile()['verified'])
        with self.store.connection() as c:
            with self.assertRaises(Problem):self.store.professional(c,self.uid)
        degree=self.upload('degree');self.approve(degree)
        self.assertTrue(self.profile()['verified'])
        with self.store.connection() as c:self.store.professional(c,self.uid)
        replacement=self.upload('degree')
        self.assertFalse(self.profile()['verified']);self.assertTrue(self.profile()['identity_approved'])
        with self.assertRaises(Problem):self.approve(degree)
        self.approve(replacement);self.assertTrue(self.profile()['verified'])
    def test_degree_alone_and_old_accreditation_cannot_replace_documents(self):
        self.approve(self.upload('degree'));self.approve(self.upload('accreditation'))
        self.assertFalse(self.profile()['verified']);self.assertFalse(self.profile()['identity_approved'])
        with self.store.connection() as c:
            with self.assertRaises(Problem):self.store.professional(c,self.uid)
    def test_name_change_invalidates_both_approvals_and_restart_preserves_requirement(self):
        self.approve(self.upload('identity'));self.approve(self.upload('degree'))
        self.store.update_profile(self.uid,{'name':'Nombre actualizado','specialty':'Psicología'})
        self.assertFalse(self.profile()['identity_approved']);self.assertFalse(self.profile()['degree_approved'])
        self.store.configure_access('AdminRulesTest2026!');self.assertFalse(self.profile()['verified'])
    def test_legacy_professional_preserves_document_exemption(self):
        self.store.import_legacy(self.admin,{'name':self.credentials['name'],'email':self.credentials['email'],'specialty':'Psicología','reference':'legacy-doc-123','sessions':10,'note':'Documentación validada anteriormente','identity_confirmed':True})
        self.assertTrue(self.profile()['verified']);self.assertEqual(self.profile()['identity_source'],'legacy')
        with self.store.connection() as c:self.store.professional(c,self.uid)

    def test_public_rules_match_checkout_and_http_cannot_skip_acceptance(self):
        import threading
        import http.client
        from http.server import ThreadingHTTPServer
        from app import Handler
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store;server.webpay=self.payments
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        token=self.store.login(self.credentials);csrf=self.store.session(token)['csrf']
        def request(path,body=None):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
            conn.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers={'Content-Type':'application/json','Cookie':'session='+token if body is not None else '', 'X-CSRF-Token':csrf})
            response=conn.getresponse();result=(response.status,response.read().decode());conn.close();return result
        try:
            status,html=request('/normas');self.assertEqual(status,200);self.assertEqual(html.count('<li>'),12);self.assertIn(RULES_VERSION,html)
            body={'catalog_id':'1','idempotency_key':'http-rules-123456789'}
            self.assertEqual(request('/api/webpay/create',body)[0],400)
            self.assertEqual(self.gateway.create_calls,0)
            self.assertEqual(request('/api/webpay/create',dict(body,rules_acceptance=ACCEPT))[0],200)
        finally:server.shutdown();server.server_close();thread.join()
