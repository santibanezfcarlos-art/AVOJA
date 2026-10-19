import concurrent.futures
import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
from app import Store, Problem, ADMIN_EMAIL, now, stamp, CHILE
from payments import WebpayGateway, PaymentService, PaymentError

class FakeGateway(WebpayGateway):
    def __init__(self):
        self.mode='integration';self.transactions={};self.create_calls=0;self.commit_calls=0
        self.status_error=False;self.create_error=False;self.commit_error=False;self.lock=threading.Lock()
    def create(self, order, session, amount):
        with self.lock:
            self.create_calls+=1
            if self.create_error:raise PaymentError('Network failure')
            token=f'{self.create_calls:064d}'
            self.transactions[token]={'buy_order':order,'session_id':session,'amount':amount,'status':'INITIALIZED','response_code':0}
            return {'token':token,'url':'https://webpay3gint.transbank.cl/webpayserver/initTransaction'}
    def status(self, token):
        if self.status_error:raise PaymentError('Network failure')
        return copy.deepcopy(self.transactions[token])
    def commit(self, token):
        with self.lock:
            self.commit_calls+=1;self.transactions[token]['status']='AUTHORIZED'
            if self.commit_error:raise PaymentError('Response lost after approval')
            return copy.deepcopy(self.transactions[token])

class MigrationAndPayments(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'data.sqlite3');self.secret='IsolatedAdminPassword2026!'
        self.store.configure_access(self.secret)
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':self.secret}))['id']
        self.client={'name':'Juanita Ejemplo','email':'juanita@example.cl','password':'ProfessionalPassword2026!','specialty':'Psicología'}
        self.uid=self.store.register(self.client)
        self.legacy={'name':self.client['name'],'email':self.client['email'],'specialty':self.client['specialty'],'reference':'old-juanita-001','sessions':10,'note':'Saldo restante revisado en sistema anterior','identity_confirmed':True}
        self.gateway=FakeGateway();self.payments=PaymentService(self.store,self.gateway)
    def verify(self):
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
    def state(self):return self.store.state(self.store.session(self.store.login(self.client)))
    def create_payment(self):
        self.verify();return self.payments.create(self.uid,'5','test-idempotency-key-001')
    def csv(self, second=''):
        return {'csv':'referencia,nombre,correo,especialidad,sesiones,observacion\nold001,Juanita Ejemplo,juanita@example.cl,Psicología,10,Saldo validado\n'+second,'identity_confirmed':True}

    def test_migrated_client_books_without_carnet_but_needs_email(self):
        self.store.import_legacy(self.admin,self.legacy)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM documents').fetchone()[0],0)
        self.assertEqual(self.state()['user']['identity_source'],'legacy')
        start=stamp((now().astimezone(CHILE)+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0))
        with self.assertRaises(Problem):self.store.reserve(self.uid,{'box':1,'start':start})
        self.verify();self.store.reserve(self.uid,{'box':1,'start':start})
        self.assertEqual(self.state()['packages'][0]['remaining'],9)
        self.store.configure_access(self.secret);self.assertEqual(self.state()['user']['verified'],1)

    def test_future_client_receives_balance_only_once_at_registration(self):
        data=dict(self.legacy,name='Cliente Futuro',email='future@example.cl',reference='old-future')
        self.store.import_legacy(self.admin,data)
        with self.store.connection() as c:self.assertIsNone(c.execute('SELECT user_id FROM legacy_imports').fetchone()[0])
        uid=self.store.register(dict(self.client,name=data['name'],email=data['email']))
        with self.store.connection(True) as c:
            self.store.attach_legacy(c,uid,data['email'])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM packages WHERE source='migration'").fetchone()[0],1)
            self.assertEqual(c.execute('SELECT email_verified FROM users WHERE id=?',(uid,)).fetchone()[0],0)
        with self.assertRaises(Problem):self.store.import_legacy(self.admin,data)

    def test_duplicate_reference_and_email_do_not_add_balance(self):
        self.store.import_legacy(self.admin,self.legacy)
        for data in (dict(self.legacy,reference='different'),dict(self.legacy,email='other@example.cl')):
            with self.assertRaises(Problem):self.store.import_legacy(self.admin,data)
        self.assertEqual(len(self.state()['packages']),1)

    def test_import_permission_attestation_and_identity_checks(self):
        with self.assertRaises(Problem):self.store.import_legacy(self.uid,self.legacy)
        for changes in ({'identity_confirmed':False},{'sessions':True},{'name':'Different Person'},{'sessions':-1}):
            with self.assertRaises(Problem):self.store.import_legacy(self.admin,self.legacy|changes)

    def test_zero_balance_and_name_change_revokes_validation(self):
        self.store.import_legacy(self.admin,dict(self.legacy,sessions=0))
        self.assertEqual(self.state()['packages'],[])
        self.store.update_profile(self.uid,{'name':'Changed Name','specialty':'Psicología'})
        self.assertEqual(self.state()['user']['verified'],0)

    def test_csv_preview_no_changes_commit_atomic_and_duplicate_safe(self):
        body=self.csv('old002,Persona Nueva,new@example.cl,Psicología,5,Saldo validado\n')
        preview=self.store.import_legacy_csv(self.admin,body,preview=True)
        self.assertEqual((preview['count'],preview['sessions']),(2,15))
        self.assertEqual(self.store.admin_state()['migrations'],[]);self.assertEqual(self.state()['packages'],[])
        self.store.import_legacy_csv(self.admin,body)
        with self.assertRaises(Problem):self.store.import_legacy_csv(self.admin,body)
        self.assertEqual(len(self.store.admin_state()['migrations']),2);self.assertEqual(len(self.state()['packages']),1)

    def test_invalid_or_duplicate_csv_row_rolls_back_entire_file(self):
        for tail in ('old002,Bad Row,bad@example.cl,Psicología,1.5,Saldo validado\n','old001,Persona Nueva,new@example.cl,Psicología,5,Saldo validado\n'):
            with self.assertRaises(Problem):self.store.import_legacy_csv(self.admin,self.csv(tail))
            self.assertEqual(self.store.admin_state()['migrations'],[]);self.assertEqual(self.state()['packages'],[])
        self.store.import_legacy_csv(self.admin,{'csv':self.csv()['csv'].replace(',',';'),'identity_confirmed':True})
        self.assertEqual(self.state()['packages'][0]['remaining'],10)

    def test_create_catalog_price_and_retries_reuse_attempt(self):
        checkout=self.create_payment()
        for key in ('test-idempotency-key-001','test-idempotency-key-002'):
            self.assertEqual(self.payments.create(self.uid,'5',key),checkout)
        with self.assertRaises(PaymentError):self.payments.create(self.uid,'10','test-idempotency-key-003')
        self.assertEqual(self.gateway.create_calls,1)
        self.assertEqual(self.state()['packages'][0]['remaining'],0);self.assertEqual(self.state()['packages'][0]['price'],28990)
        self.assertNotIn(checkout['token'],json.dumps(self.store.admin_state()))

    def test_approval_credits_once_and_repeat_does_not_restore_spent_hours(self):
        checkout=self.create_payment();token=checkout['token']
        self.assertEqual(self.payments.returned({'token_ws':token})['state'],'approved')
        with self.store.connection(True) as c:c.execute('UPDATE packages SET remaining=remaining-1 WHERE user_id=?',(self.uid,))
        self.payments.returned({'token_ws':token})
        self.assertEqual(self.state()['packages'][0]['remaining'],4);self.assertEqual(self.gateway.commit_calls,1)
        with self.assertRaises(PaymentError):self.payments.reconcile(checkout['payment_id'],uid=self.admin)
        with self.assertRaises(Problem):self.store.admin_action(self.admin,{'action':'payment','id':self.state()['packages'][0]['id'],'decision':'paid'})

    def test_forged_abandonment_and_unknown_returns_never_credit(self):
        checkout=self.create_payment();self.payments.returned({'TBK_TOKEN':checkout['token']})
        self.assertEqual(self.gateway.commit_calls,0);self.assertEqual(self.state()['packages'][0]['remaining'],0)
        for fields in ({'token_ws':'x'*64},{'token_ws':checkout['token'],'TBK_TOKEN':checkout['token']},{'TBK_ORDEN_COMPRA':'fake','TBK_ID_SESION':'fake'}):
            with self.assertRaises(PaymentError):self.payments.returned(fields)

    def test_declined_payment_never_credits(self):
        checkout=self.create_payment();self.gateway.transactions[checkout['token']].update(status='FAILED',response_code=-1)
        self.assertEqual(self.payments.returned({'token_ws':checkout['token']})['state'],'rejected')
        self.assertEqual(self.state()['packages'][0]['remaining'],0)

    def test_mismatch_or_invalid_authorization_never_credits(self):
        checkout=self.create_payment();original=copy.deepcopy(self.gateway.transactions[checkout['token']])
        for values in ({'buy_order':'forged'},{'session_id':'forged'},{'amount':1},{'response_code':False},{'response_code':None}):
            self.gateway.transactions[checkout['token']]=dict(original,status='AUTHORIZED')|values
            try:self.payments.returned({'token_ws':checkout['token']})
            except PaymentError:pass
            self.assertEqual(self.state()['packages'][0]['remaining'],0)

    def test_lost_commit_response_recovers_without_second_charge(self):
        checkout=self.create_payment();self.gateway.commit_error=True
        with self.assertRaises(PaymentError):self.payments.returned({'token_ws':checkout['token']})
        self.assertEqual(self.state()['packages'][0]['remaining'],0)
        self.assertEqual(self.payments.reconcile(checkout['payment_id'],uid=self.uid)['state'],'approved')
        self.assertEqual(self.state()['packages'][0]['remaining'],5);self.assertEqual(self.gateway.commit_calls,1)

    def test_pending_network_error_does_not_credit_or_allow_new_payment(self):
        checkout=self.create_payment();self.gateway.status_error=True
        with self.assertRaises(PaymentError):self.payments.reconcile(checkout['payment_id'],uid=self.uid)
        with self.assertRaises(PaymentError):self.payments.create(self.uid,'5','another-idempotency-key')
        self.assertEqual(self.state()['packages'][0]['remaining'],0)
        self.gateway.status_error=False
        self.assertEqual(self.payments.reconcile(checkout['payment_id'],uid=self.uid)['state'],'ready')

    def test_creation_timeout_does_not_automatically_retry(self):
        self.verify();self.gateway.create_error=True
        for _ in range(2):
            with self.assertRaises(PaymentError):self.payments.create(self.uid,'5','timeout-idempotency-key')
        self.assertEqual(self.gateway.create_calls,1);self.assertEqual(self.state()['packages'][0]['remaining'],0)

    def test_parallel_returns_do_not_double_credit(self):
        checkout=self.create_payment()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:self.payments.returned({'token_ws':checkout['token']}),range(4)))
        self.assertEqual(self.state()['packages'][0]['remaining'],5);self.assertEqual(self.gateway.commit_calls,1)

    def test_background_reconciliation_credits_approved_payment_without_browser_return(self):
        checkout=self.create_payment()
        self.gateway.transactions[checkout['token']]['status']='AUTHORIZED'
        self.payments.reconcile_pending()
        self.assertEqual(self.state()['packages'][0]['remaining'],5)
        self.payments.reconcile_pending()
        self.assertEqual(self.state()['packages'][0]['remaining'],5)

    def test_callback_during_status_lease_keeps_commit_request_for_later(self):
        import time
        checkout=self.create_payment()
        with self.store.connection(True) as c:
            c.execute('UPDATE payment_attempts SET processing_until=?',(time.time()+60,))
        self.assertEqual(self.payments.returned({'token_ws':checkout['token']})['state'],'processing')
        with self.store.connection(True) as c:c.execute('UPDATE payment_attempts SET processing_until=0')
        self.payments.reconcile_pending()
        self.assertEqual(self.state()['packages'][0]['remaining'],5)

    def test_unverified_email_cannot_start_payment(self):
        with self.assertRaises(PaymentError):self.payments.create(self.uid,'5','unverified-idempotency-key')
        self.assertEqual(self.gateway.create_calls,0)

class GatewayProtocol(unittest.TestCase):
    def test_default_disabled_production_rejected(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(WebpayGateway().mode,'disabled')
            with self.assertRaises(PaymentError):WebpayGateway().create('order','session',5990)
        with patch.dict(os.environ,{'VALHALLA_WEBPAY_MODE':'production'},clear=True):
            with self.assertRaises(ValueError):WebpayGateway()

    def test_fixed_https_protocol_and_destination(self):
        with patch.dict(os.environ,{'VALHALLA_WEBPAY_MODE':'integration'},clear=True):
            gateway=WebpayGateway();response=MagicMock();response.__enter__.return_value.read.return_value=b'{"status":"INITIALIZED"}'
            with patch.object(gateway.opener,'open',return_value=response) as call:
                gateway.create('VHorder','session',5990)
                request=call.call_args.args[0];self.assertEqual(request.full_url,gateway.endpoint);self.assertEqual(request.method,'POST')
                self.assertEqual(json.loads(request.data)['amount'],5990);self.assertEqual(request.get_header('Tbk-api-key-id'),'597055555532')
                gateway.status('a'*64);self.assertEqual(call.call_args.args[0].method,'GET')
                gateway.commit('a'*64);self.assertEqual(call.call_args.args[0].method,'PUT')
            self.assertTrue(gateway.valid_checkout('https://webpay3gint.transbank.cl/webpayserver/initTransaction'))
            for url in ('https://evil.example/webpayserver/initTransaction','http://webpay3gint.transbank.cl/webpayserver/initTransaction','https://webpay3gint.transbank.cl@evil.example/webpayserver/initTransaction'):
                self.assertFalse(gateway.valid_checkout(url))

class NewEndpointPermissions(unittest.TestCase):
    def setUp(self):
        import urllib.request
        from http.server import ThreadingHTTPServer
        from app import Handler
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'http.sqlite3');self.store.configure_access('PrivateAdminPassword2026!')
        self.credentials={'name':'HTTP Professional','email':'http@example.cl','password':'ProfessionalPassword2026!','specialty':'Psicología'}
        self.uid=self.store.register(self.credentials);token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.session=self.store.login(self.credentials);self.csrf=self.store.session(self.session)['csrf']
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=self.store
        self.gateway=FakeGateway();self.server.webpay=PaymentService(self.store,self.gateway)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=urllib.request.build_opener(__import__('payments').NoRedirect())
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,authenticated=True,csrf=True,form=False,origin=None):
        import urllib.request,urllib.error,urllib.parse
        headers={'Content-Type':'application/x-www-form-urlencoded' if form else 'application/json'}
        if authenticated:headers['Cookie']='session='+self.session
        if csrf:headers['X-CSRF-Token']=self.csrf
        if origin:headers['Origin']=origin
        raw=urllib.parse.urlencode(body).encode() if body is not None and form else json.dumps(body).encode() if body is not None else None
        try:response=self.opener.open(urllib.request.Request(self.base+path,data=raw,headers=headers))
        except urllib.error.HTTPError as error:response=error
        raw=response.read();data=json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else raw
        return response.status,data,response.headers
    def test_migration_requires_admin_and_csrf(self):
        for path in ('/api/admin/migrations','/api/admin/migrations/preview','/api/admin/migrations/import'):
            self.assertEqual(self.request(path,{},authenticated=False)[0],401)
            self.assertEqual(self.request(path,{},csrf=False)[0],403)
            self.assertEqual(self.request(path,{})[0],403)
        self.assertEqual(self.store.admin_state()['migrations'],[])
    def test_payment_permissions_server_price_and_no_manual_approval(self):
        body={'catalog_id':'5','idempotency_key':'http-test-idempotency-key','amount':1,'hours':999,'user_id':999}
        self.assertEqual(self.request('/api/webpay/create',body,authenticated=False)[0],401)
        self.assertEqual(self.request('/api/webpay/create',body,csrf=False)[0],403)
        self.assertEqual(self.request('/api/webpay/create',body,origin='https://evil.example')[0],403)
        status,checkout,_=self.request('/api/webpay/create',body);self.assertEqual(status,200)
        self.assertEqual(self.gateway.transactions[checkout['token']]['amount'],28990)
        self.assertEqual(self.request('/api/packages',{'catalog_id':'5'})[0],409)
        self.assertEqual(self.request('/api/admin/webpay/reconcile',{'id':checkout['payment_id']})[0],403)
        self.assertEqual(self.request('/api/webpay/reconcile',{'id':999})[0],404)
    def test_cross_site_bank_return_does_not_need_session_or_csrf_and_credits_once(self):
        _,checkout,_=self.request('/api/webpay/create',{'catalog_id':'5','idempotency_key':'http-bank-return-test'})
        result=self.request('/api/webpay/return',{'token_ws':checkout['token']},authenticated=False,csrf=False,form=True,origin='https://webpay3gint.transbank.cl')
        self.assertEqual(result[0],303);self.assertEqual(result[2]['Location'],'/?payment_result=approved')
        self.assertNotIn('Set-Cookie',result[2]);self.assertNotIn(checkout['token'],str(result))
        self.request('/api/webpay/return?token_ws='+checkout['token'],authenticated=False,csrf=False)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT remaining FROM packages').fetchone()[0],5)
        self.assertEqual(self.gateway.commit_calls,1)

    def test_daily_contacts_is_exclusively_admin_and_rejects_invalid_dates(self):
        path='/api/admin/daily-contacts?date=2026-10-22'
        self.assertEqual(self.request(path,authenticated=False)[0],401)
        self.assertEqual(self.request(path)[0],403)
        self.session=self.store.login({'email':ADMIN_EMAIL,'password':'PrivateAdminPassword2026!'})
        status,data,_=self.request(path)
        self.assertEqual(status,200);self.assertEqual(data['emails'],[])
        self.assertEqual(self.request('/api/admin/daily-contacts?date=bad')[0],400)
