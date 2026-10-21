import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from app import Store,ADMIN_EMAIL,Problem
from payments import WebpayGateway,PaymentService,PaymentError
from rules import RULES_VERSION
from test_migration_payments import FakeGateway

class FakeProductionGateway(FakeGateway):
    def __init__(self):
        super().__init__();self.mode='production';self.checkout_host='webpay3g.transbank.cl';self.commerce_code='597000000001';self.public_url='https://valhallaoffice.cl'
    def create(self,*args):
        data=super().create(*args);old=data['token'];data['token']='p'+old[1:];self.transactions[data['token']]=self.transactions.pop(old);data['url']='https://webpay3g.transbank.cl/webpayserver/initTransaction';return data

class ProductionPayments(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'production.sqlite3');self.store.configure_access('ProductionAdmin2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'ProductionAdmin2026!'}))['id']
        self.uid=self.store.register({'name':'Profesional Real','email':'real@example.cl','specialty':'Psicología','password':'ProductionPro2026!'})
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.gateway=FakeProductionGateway();self.service=PaymentService(self.store,self.gateway);self.rules={'accepted':True,'version':RULES_VERSION}
    def validation(self):return self.service.create(self.admin,'validation_50','production-validation-123456',self.rules,'',50)
    def enable(self):
        checkout=self.validation();self.service.returned({'token_ws':checkout['token']});self.service.set_live_sales(self.admin,{'enabled':True,'confirmed':True});return checkout
    def test_production_requires_explicit_credentials_and_fixed_destination(self):
        env={'VALHALLA_WEBPAY_MODE':'production','VALHALLA_WEBPAY_COMMERCE_CODE':'597000000001','VALHALLA_WEBPAY_API_KEY':'synthetic-private-test-key','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'}
        with patch.dict(os.environ,env,clear=True):
            g=WebpayGateway();self.assertEqual(g.checkout_host,'webpay3g.transbank.cl');self.assertTrue(g.valid_checkout('https://webpay3g.transbank.cl/webpayserver/initTransaction'));self.assertFalse(g.valid_checkout('https://webpay3gint.transbank.cl/webpayserver/initTransaction'))
            response=MagicMock();response.__enter__.return_value.read.return_value=b'{"status":"INITIALIZED"}'
            with patch.object(g.opener,'open',return_value=response) as opening:
                g.status('x'*64);request=opening.call_args[0][0];self.assertTrue(request.full_url.startswith('https://webpay3g.transbank.cl/'));self.assertEqual(request.get_header('Tbk-api-key-secret'),'synthetic-private-test-key')
        for changes in ({'VALHALLA_WEBPAY_API_KEY':''},{'VALHALLA_WEBPAY_COMMERCE_CODE':'53106860'},{'VALHALLA_PUBLIC_URL':'http://unsafe.example'}):
            with patch.dict(os.environ,env|changes,clear=True),self.assertRaises(ValueError):WebpayGateway()
    def test_database_sandbox_setting_cannot_override_production(self):
        with self.store.connection(True) as c:c.execute("INSERT INTO webpay_settings VALUES(1,'old',1,'now')")
        self.service.restore_configuration();self.assertEqual(self.service.mode,'production')
        with self.assertRaises(PaymentError):self.service.configure(self.admin,{'commerce_code':'597000000001','sandbox_enabled':True})
    def test_owner_validation_charges_50_but_no_hours_and_unlocks_explicit_sales(self):
        with self.assertRaises(PaymentError):self.service.create(self.uid,'1','production-before-validation',self.rules)
        with self.assertRaises(PaymentError):self.service.set_live_sales(self.admin,{'enabled':True,'confirmed':True})
        checkout=self.validation();self.assertEqual(self.gateway.transactions[checkout['token']]['amount'],50)
        with self.assertRaises(PaymentError):self.service.set_live_sales(self.admin,{'enabled':True,'confirmed':True})
        self.service.returned({'token_ws':checkout['token']});self.service.returned({'token_ws':checkout['token']})
        with self.store.connection() as c:
            p=c.execute('SELECT * FROM packages WHERE user_id=?',(self.admin,)).fetchone();self.assertEqual((p['hours'],p['remaining'],p['price'],p['status']),(0,0,50,'paid'))
        self.assertTrue(self.service.configuration()['validation_paid']);self.assertFalse(self.service.configuration()['live_sales_enabled'])
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_outbox WHERE kind='hours_credited'").fetchone()[0],0)
        self.service.set_live_sales(self.admin,{'enabled':True,'confirmed':True});self.assertTrue(self.service.configuration()['live_sales_enabled'])
    def test_validation_is_owner_only_fixed_price_no_discount_or_forged_hours(self):
        with self.assertRaises(PaymentError):self.service.create(self.uid,'validation_50','forged-validation-owner',self.rules)
        with self.assertRaises(PaymentError):self.service.create(self.admin,'validation_50','discount-validation-owner',self.rules,'ANY')
        with self.assertRaises(PaymentError):self.service.create(self.admin,'validation_50','price-validation-owner',self.rules,'',1)
        self.assertEqual(self.gateway.create_calls,0)
    def test_live_discount_payment_credits_once_and_pause_does_not_stop_reconciliation(self):
        self.enable();self.store.save_discount(self.admin,{'code':'REAL20','percent':20})
        checkout=self.service.create(self.uid,'5','live-normal-purchase-12345',self.rules,'REAL20',23192)
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_outbox WHERE kind='hours_credited'").fetchone()[0],0)
        self.service.set_live_sales(self.admin,{'enabled':False,'confirmed':True})
        self.service.returned({'token_ws':checkout['token']});self.service.returned({'token_ws':checkout['token']})
        with self.store.connection() as c:
            p=c.execute('SELECT * FROM packages WHERE user_id=?',(self.uid,)).fetchone();self.assertEqual((p['hours'],p['remaining'],p['price'],p['source']),(5,5,23192,'webpay_production'))
            self.assertTrue(c.execute('SELECT approved_at FROM payment_attempts WHERE user_id=?',(self.uid,)).fetchone()[0])
            import json
            receipts=c.execute("SELECT * FROM notification_outbox WHERE kind='hours_credited' AND user_id=?",(self.uid,)).fetchall()
            self.assertEqual(len(receipts),1)
            self.assertEqual(json.loads(receipts[0]['payload'])['hours'],5)
            self.assertEqual(json.loads(receipts[0]['payload'])['source'],'production')
        with self.assertRaises(PaymentError):self.service.create(self.uid,'1','paused-live-purchase-12345',self.rules)
    def test_pending_integration_does_not_block_production_or_switch_its_host(self):
        sandbox=PaymentService(self.store,FakeGateway());sandbox.create(self.uid,'1','pending-sandbox-before-live',self.rules)
        self.enable();checkout=self.service.create(self.uid,'1','new-live-after-sandbox-123',self.rules)
        self.assertEqual(checkout['mode'],'production');self.assertIn('webpay3g.transbank.cl',checkout['url'])
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM payment_attempts WHERE user_id=? AND state='ready'",(self.uid,)).fetchone()[0],2)
    def test_mixed_tokens_and_timeout_do_not_commit_even_with_previous_commit_flag(self):
        checkout=self.validation();token=checkout['token'];tx=self.gateway.transactions[token]
        with self.store.connection(True) as c:c.execute('UPDATE payment_attempts SET commit_requested=1')
        result=self.service.returned({'token_ws':token,'TBK_TOKEN':token,'TBK_ORDEN_COMPRA':tx['buy_order'],'TBK_ID_SESION':tx['session_id']})
        self.assertEqual(result['state'],'ready');self.service.returned({'token_ws':'another-normal-token-not-used','TBK_TOKEN':token,'TBK_ORDEN_COMPRA':tx['buy_order'],'TBK_ID_SESION':tx['session_id']});self.service.reconcile_pending();self.assertEqual(self.gateway.commit_calls,0)
        self.service.returned({'TBK_ORDEN_COMPRA':tx['buy_order'],'TBK_ID_SESION':tx['session_id']});self.assertEqual(self.gateway.commit_calls,0)
        with self.assertRaises(PaymentError):self.service.returned({'token_ws':token,'TBK_TOKEN':token,'TBK_ORDEN_COMPRA':'wrong'})
        self.assertFalse(self.service.live_configuration()['validation_paid'])
    def test_merchant_change_cannot_reuse_previous_validation_or_sales(self):
        self.enable();self.gateway.commerce_code='597000000002'
        self.assertFalse(self.service.live_configuration()['validation_paid']);self.assertFalse(self.service.live_configuration()['sales_enabled'])
        with self.assertRaises(PaymentError):self.service.set_live_sales(self.admin,{'enabled':True,'confirmed':True})
    def test_live_controls_are_owner_only_and_confirmation_required(self):
        self.enable()
        with self.assertRaises(Problem):self.service.set_live_sales(self.uid,{'enabled':True,'confirmed':True})
        with self.assertRaises(PaymentError):self.service.set_live_sales(self.admin,{'enabled':True})
        self.assertNotIn('api_key',str(self.service.configuration()).lower());self.assertNotIn('synthetic-private-test-key',str(self.service.configuration()))
