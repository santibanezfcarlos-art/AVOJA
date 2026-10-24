import base64
import concurrent.futures
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
import billing
import dte
from dte_provider import Duplicate, Uncertain, OpenFactura, ProviderError
import dte_mail
import main

PDF=base64.b64encode(b'%PDF-1.4\n% simulated fixture').decode()
class FakeProvider:
    calls=0
    fail=False
    duplicate=False
    status='Aceptado'
    body=None
    def __init__(self,mode): pass
    def issue(self,body,key):
        type(self).calls+=1
        type(self).body=body
        if self.fail: raise Uncertain()
        if self.duplicate: raise Duplicate('a'*64)
        return {'TOKEN':'a'*64}
    def get(self,token,value):
        doc=json.loads(json.dumps(self.body['dte']))
        doc['Encabezado']['IdDoc']['Folio']=123
        return {'json':doc,'pdf':PDF,'xml':'fixture','estado':self.status}

class DTETests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PSICOLOGIA_BILLING_DB':self.tmp.name+'/ledger.db',
          'PSICOLOGIA_BILLING_ADMIN_PASSWORD':'test-password-long-enough','PSICOLOGIA_BILLING_ADMIN_HASH':'',
          'RENDER':'','PSC_DTE_MODE':'integration','PSC_DTE_EMAIL_ENABLED':'false',
          'PSC_DTE_EXEMPT_SERVICES':'psicoterapia','PSC_DTE_ISSUER_JSON':json.dumps(dte.SANDBOX_ISSUER),
          'PSICOLOGIA_CHECKOUT_MODE':'production','PSICOLOGIA_PRODUCTION_APPROVED':'true',
          'PSICOLOGIA_PRODUCTION_COMMERCE_CODE':'123456789012','PSICOLOGIA_PRODUCTION_API_KEY':'x'*32})
        self.env.start()
        self.fake=patch.object(dte,'OpenFactura',FakeProvider);self.fake.start()
        FakeProvider.calls=0;FakeProvider.fail=False;FakeProvider.duplicate=False;FakeProvider.status='Aceptado'
        self.person={'rut':'12345678-5','name':'PERSONA PRIVADA','address':'DIRECCION PRIVADA',
                     'email':'private@example.invalid','phone':'123456789'}
        self.client=main.app.test_client()
        self.auth=('carlos','test-password-long-enough')
    def tearDown(self):
        self.fake.stop();self.env.stop();self.tmp.cleanup()
    def order(self,name='order1',env='integration',paid=True):
        billing.prepare(name,'psicoterapia','','',30000,self.person,env)
        billing.bind_token(name,'t'*40)
        dte.bind_session(name,'session1234')
        if paid: billing.record_result(name,'t'*40,30000,True,'VD')
        return name
    def job(self,order='order1'):
        with billing.db() as con:
            row=con.execute('SELECT * FROM dte_jobs WHERE order_id=?',(order,)).fetchone()
            return dict(row) if row else None
    def approved(self):
        return {'status':'AUTHORIZED','response_code':0,'buy_order':'order1',
                'session_id':'session1234','amount':30000}
    def confirm(self,result=None):
        with patch('webpay_gateway.Gateway.status',return_value=result or self.approved()):
            dte.confirm_production_payment('order1','t'*40)
    def test_default_disabled_no_network(self):
        with patch.dict(os.environ,{'PSC_DTE_MODE':'disabled'}):
            self.assertFalse(dte.process_one())
        self.assertEqual(FakeProvider.calls,0)
    def test_simulation_minimizes_patient_data_and_deduplicates(self):
        self.order();dte.enqueue_simulation('order1');dte.enqueue_simulation('order1')
        self.assertNotIn('PRIVADA',self.job()['payload'])
        self.assertTrue(dte.process_one());self.assertFalse(dte.process_one())
        self.assertEqual(FakeProvider.calls,1)
        with billing.db() as con:
            r=con.execute('SELECT invoice_status,folio FROM billing_orders').fetchone()
            self.assertEqual(tuple(r),('test',None))
    def test_production_rejects_unverified_and_sandbox(self):
        self.order(env='production')
        with self.assertRaises(ValueError):dte.enqueue_simulation('order1')
        with billing.db() as con:
            row=con.execute('SELECT * FROM billing_orders').fetchone()
            with self.assertRaises(ValueError):dte._enqueue(con,row)
        self.assertIsNone(self.job())
    def test_verified_payment_transaction_and_replay(self):
        self.order(env='production',paid=False)
        self.confirm();self.confirm()
        self.assertEqual(self.job()['verified_payment'],1)
        with patch.dict(os.environ,{'PSC_DTE_MODE':'production'}):dte.process_one()
        with billing.db() as con:
            r=con.execute('SELECT payment_status,invoice_status,folio FROM billing_orders').fetchone()
            self.assertEqual(tuple(r),('approved','issued','123'))
    def test_wrong_amount_session_status_response_never_queues(self):
        self.order(env='production',paid=False)
        for k,v in [('amount',1),('session_id','wrong'),('buy_order','wrong'),('status','FAILED'),('response_code',False),('response_code',1)]:
            with self.assertRaises(ValueError):self.confirm({**self.approved(),k:v})
        self.assertIsNone(self.job())
    def test_exemption_not_assumed(self):
        self.order(env='production',paid=False)
        with patch.dict(os.environ,{'PSC_DTE_EXEMPT_SERVICES':''}):
            with self.assertRaises(ValueError):self.confirm()
        self.assertIsNone(self.job())
    def test_timeout_retries_same_key_then_stops_after_window(self):
        self.order();dte.enqueue_simulation('order1');key=self.job()['idempotency_key']
        FakeProvider.fail=True;dte.process_one()
        self.assertEqual(self.job()['state'],'retry')
        with billing.db() as con:
            con.execute('UPDATE dte_jobs SET next_attempt=0,first_attempt=?',(time.time()-24*3600,))
        dte.process_one()
        self.assertEqual(self.job()['state'],'reconciliation')
        self.assertEqual(FakeProvider.calls,1);self.assertEqual(key,self.job()['idempotency_key'])
    def test_duplicate_provider_response_recovers_by_get(self):
        self.order();dte.enqueue_simulation('order1');FakeProvider.duplicate=True
        dte.process_one();self.assertEqual(self.job()['state'],'accepted')
    def test_pending_sii_retried_with_get_only(self):
        self.order();dte.enqueue_simulation('order1');FakeProvider.status='Pendiente'
        dte.process_one();self.assertEqual(self.job()['state'],'submitted')
        with billing.db() as con:con.execute('UPDATE dte_jobs SET next_attempt=0')
        FakeProvider.status='Aceptado';dte.process_one()
        self.assertEqual(FakeProvider.calls,1)
    def test_concurrent_workers_issue_once(self):
        self.order();dte.enqueue_simulation('order1')
        with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:dte.process_one(),range(2)))
        self.assertEqual(FakeProvider.calls,1)
    def test_stale_worker_lease_recovers_same_key(self):
        self.order();dte.enqueue_simulation('order1')
        with billing.db() as con:con.execute("UPDATE dte_jobs SET state='processing',lease_until=1,first_attempt=?",(time.time()-20,))
        dte.process_one();self.assertEqual(self.job()['state'],'accepted')
    def test_private_pdf_no_public_access(self):
        self.order();dte.enqueue_simulation('order1');dte.process_one()
        self.assertEqual(self.client.get('/admin/boletas/order1/pdf').status_code,401)
        res=self.client.get('/admin/boletas/order1/pdf',auth=self.auth)
        self.assertEqual(res.status_code,200);self.assertIn('SIMULACION',res.headers['Content-Disposition'])
    def test_manual_issuance_blocked_when_queued(self):
        self.order(env='production');self.confirm()
        r=self.client.post('/admin/boletas/order1/issued',auth=self.auth,
          headers={'X-Billing-Action':'manual-invoice'},json={'folio':'55','issued_date':billing.now()[:10]})
        self.assertEqual(r.status_code,409)
    def test_no_email_from_simulations(self):
        self.order();dte.enqueue_simulation('order1');dte.process_one()
        with patch.dict(os.environ,{'PSC_DTE_EMAIL_ENABLED':'true'}),patch('smtplib.SMTP_SSL') as smtp:
            self.assertFalse(dte_mail.deliver_one());smtp.assert_not_called()
    def test_provider_production_gate(self):
        with patch.dict(os.environ,{'PSC_DTE_PRODUCTION_APPROVED':'false'}):
            with self.assertRaises(ProviderError):OpenFactura('production')
    def test_rejected_document_never_emails(self):
        self.order();dte.enqueue_simulation('order1');FakeProvider.status='Rechazado';dte.process_one()
        self.assertEqual(self.job()['state'],'rejected')

    def test_paid_receipt_survives_missing_tax_config_and_worker_recovers(self):
        self.order(env='production',paid=False)
        with patch.dict(os.environ,{'PSC_DTE_EXEMPT_SERVICES':''}):
            with self.assertRaises(ValueError):self.confirm()
        with billing.db() as con:
            self.assertEqual(con.execute('SELECT payment_status FROM billing_orders').fetchone()[0],'approved')
        dte.enqueue_verified_pending()
        self.assertEqual(self.job()['verified_payment'],1)
    def test_email_failure_never_reissues_document(self):
        self.order(env='production',paid=False);self.confirm()
        settings={'PSC_DTE_MODE':'production','PSC_DTE_EMAIL_ENABLED':'true',
          'PSC_DTE_SMTP_FROM':'test@example.invalid','PSC_DTE_SMTP_HOST':'smtp.example.invalid',
          'PSC_DTE_SMTP_USER':'test','PSC_DTE_SMTP_PASSWORD':'test'}
        with patch.dict(os.environ,settings):
            dte.process_one()
            with patch('smtplib.SMTP_SSL',side_effect=TimeoutError()):dte_mail.deliver_one()
            self.assertEqual(self.job()['email_state'],'review')
            self.assertFalse(dte.process_one())
            self.assertEqual(FakeProvider.calls,1)
    def test_email_success_contains_pdf_and_delivers_once(self):
        self.order(env='production',paid=False);self.confirm()
        settings={'PSC_DTE_MODE':'production','PSC_DTE_EMAIL_ENABLED':'true',
          'PSC_DTE_SMTP_FROM':'test@example.invalid','PSC_DTE_SMTP_HOST':'smtp.example.invalid',
          'PSC_DTE_SMTP_USER':'test','PSC_DTE_SMTP_PASSWORD':'test'}
        with patch.dict(os.environ,settings),patch('smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.return_value={}
            dte.process_one();self.assertTrue(dte_mail.deliver_one());self.assertFalse(dte_mail.deliver_one())
            message=smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(list(message.iter_attachments())[0].get_content_type(),'application/pdf')
            self.assertEqual(self.job()['email_state'],'sent')
    def test_expired_retry_and_csrf_are_blocked(self):
        self.order();dte.enqueue_simulation('order1')
        with billing.db() as con:
            con.execute("UPDATE dte_jobs SET state='reconciliation',first_attempt=?",(time.time()-86400,))
        url='/admin/boletas/order1/retry'
        self.assertEqual(self.client.post(url,auth=self.auth).status_code,403)
        self.assertEqual(self.client.post(url,auth=self.auth,headers={'X-Billing-Action':'manual-invoice'}).status_code,409)
    def test_document_mismatch_is_quarantined(self):
        self.order();dte.enqueue_simulation('order1')
        original=FakeProvider.get
        def bad(provider,token,value):
            result=original(provider,token,value)
            result['json']['Encabezado']['Totales']['MntTotal']=1
            return result
        with patch.object(FakeProvider,'get',bad):dte.process_one()
        self.assertEqual(self.job()['state'],'review')

if __name__=='__main__':unittest.main()
