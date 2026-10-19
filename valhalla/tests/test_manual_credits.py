import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
from app import Store,Problem,ADMIN_EMAIL,CHILE,stamp
from rules import RULES_VERSION

class ManualCredits(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'manual.sqlite3');self.store.configure_access('ManualAdmin2026!')
        self.admin_token=self.store.login({'email':ADMIN_EMAIL,'password':'ManualAdmin2026!'})
        self.admin=self.store.session(self.admin_token)['id']
        self.credentials={'name':'Profesional Efectivo','email':'cash@example.cl','specialty':'Psicología','password':'ManualProfessional2026!'}
        self.uid=self.store.register(self.credentials)
        self.token=self.store.login(self.credentials)
        self.body={'user_id':self.uid,'hours':5,'amount':28990,'method':'cash','reference':'CAJA-20261019-001','note':'Recibido en sucursal','confirmed':True,'request_key':'manual-request-123456789'}
    def credit(self,**changes):return self.store.manual_credit(self.admin,self.body|changes)
    def state(self):return self.store.state(self.store.session(self.token))
    def test_credit_ready_no_bank_transaction_and_no_forged_client_consent_or_validation(self):
        pid=self.credit()['package_id'];state=self.state()
        self.assertEqual(state['packages'][0]['id'],pid);self.assertEqual(state['packages'][0]['remaining'],5)
        self.assertEqual(state['packages'][0]['status'],'paid');self.assertEqual(state['packages'][0]['manual_method'],'cash')
        self.assertFalse(state['packages'][0]['rules_accepted']);self.assertEqual(state['user']['email_verified'],0);self.assertFalse(state['user']['verified'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM payment_attempts').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM purchase_acceptances').fetchone()[0],0)
        record=self.store.admin_state()['manual_credits'][0]
        self.assertEqual(record['operator_name'],'Administración Valhalla');self.assertEqual(record['reference'],self.body['reference'])
        self.assertNotIn('manual_credits',state)
    def test_receipt_date_preserved_and_future_invalid_rejected(self):
        self.credit(received_on='2025-09-30')
        self.assertEqual(self.store.admin_state()['manual_credits'][0]['received_on'],'2025-09-30')
        for invalid in ('2025-02-30','2099-01-01','wrong',None,123):
            with self.subTest(invalid=invalid),self.assertRaises(Problem):
                self.credit(received_on=invalid,reference='invalid-date',request_key='invalid-date-request-12345')

    def test_transfer_and_courtesy_keep_recorded_amount(self):
        self.credit(method='transfer',reference='Banco-20261019-001')
        self.credit(method='courtesy',amount=0,reference='CORTESIA-001',request_key='manual-request-courtesy-123')
        self.assertEqual({p['price'] for p in self.state()['packages']},{0,28990})
    def test_only_admin_can_credit_and_only_active_existing_account(self):
        with self.assertRaises(Problem) as error:self.store.manual_credit(self.uid,self.body)
        self.assertEqual(error.exception.status,403)
        for uid in (99999,True):
            with self.assertRaises(Problem):self.credit(user_id=uid)
        with self.store.connection(True) as c:c.execute('UPDATE users SET active=0 WHERE id=?',(self.uid,))
        with self.assertRaises(Problem):self.credit()
    def test_invalid_amount_hours_method_and_attestation_do_not_create_balance(self):
        for changes in ({'hours':0},{'hours':201},{'hours':True},{'hours':2.5},{'amount':-1},{'amount':True},{'amount':0},{'method':'courtesy'},{'method':'card'},{'confirmed':False},{'reference':''},{'note':'x'},{'request_key':'short'}):
            with self.subTest(changes=changes),self.assertRaises(Problem):self.credit(**changes)
        self.assertEqual(self.state()['packages'],[])
    def test_retry_and_reference_duplicate_cannot_credit_twice(self):
        result=self.credit();self.assertEqual(self.credit()['package_id'],result['package_id']);self.assertTrue(self.credit()['repeated'])
        with self.assertRaises(Problem):self.credit(hours=10)
        with self.assertRaises(Problem):self.credit(request_key='another-manual-key-123',reference=self.body['reference'].lower())
        self.assertEqual(len(self.state()['packages']),1)
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='manual_hours_credited'").fetchone()[0],1)
    def test_parallel_different_requests_same_reference_only_one_credits(self):
        def credit(n):
            try:self.credit(request_key=f'manual-parallel-{n}-123456');return True
            except Problem:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(credit,[1,2]))
        self.assertEqual(sum(results),1);self.assertEqual(len(self.state()['packages']),1)
    def test_professional_accepts_own_rules_then_can_use_hours_after_validation(self):
        pid=self.credit()['package_id'];email_token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(email_token)
        self.store.import_legacy(self.admin,{'name':self.credentials['name'],'email':self.credentials['email'],'specialty':'Psicología','sessions':0,'reference':'old-cash-001','note':'Cliente validado anteriormente','identity_confirmed':True})
        slot={'box':1,'start':stamp((datetime.now(CHILE)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0))}
        with self.assertRaises(Problem) as error:self.store.reserve(self.uid,slot)
        self.assertIn('normas',error.exception.message)
        with self.assertRaises(Problem):self.store.accept_manual_rules(self.admin,{'package_id':pid,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        self.store.accept_manual_rules(self.uid,{'package_id':pid,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        self.assertTrue(self.state()['packages'][0]['rules_accepted']);self.store.reserve(self.uid,slot)
        self.assertEqual(self.state()['packages'][0]['remaining'],4)
        self.store.accept_manual_rules(self.uid,{'package_id':pid,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM purchase_acceptances WHERE package_id=?',(pid,)).fetchone()[0],1)
    def test_http_admin_route_requires_session_csrf_and_no_private_reference_for_professional(self):
        import threading,http.client,json
        from http.server import ThreadingHTTPServer
        from app import Handler
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(token='',csrf=''):
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('POST','/api/admin/manual-credits',json.dumps(self.body),{'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf});response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            self.assertEqual(request()[0],401);self.assertEqual(request(self.token,self.store.session(self.token)['csrf'])[0],403)
            self.assertEqual(request(self.admin_token)[0],403)
            self.assertEqual(request(self.admin_token,self.store.session(self.admin_token)['csrf'])[0],200)
            self.assertNotIn(self.body['reference'],json.dumps(self.state()))
        finally:server.shutdown();server.server_close();thread.join()
