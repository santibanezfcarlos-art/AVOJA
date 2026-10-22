import tempfile
import unittest
from pathlib import Path
from app import Store, Problem, ADMIN_EMAIL
from payments import PaymentService, PaymentError
from rules import RULES_VERSION
from test_migration_payments import FakeGateway

class Discounts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'discount.sqlite3');self.store.configure_access('DiscountAdmin2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'DiscountAdmin2026!'}))['id']
        self.uid=self.store.register({'name':'Profesional Descuento','email':'discount@example.cl','specialty':'Psicología','password':'DiscountPro2026!'})
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.rules={'accepted':True,'version':RULES_VERSION}
        self.gateway=FakeGateway();self.pay=PaymentService(self.store,self.gateway)
        self.save()
    def save(self,**changes):return self.store.save_discount(self.admin,{'code':'BIENVENIDA20','percent':20}|changes)
    def quote(self,code='BIENVENIDA20'):
        with self.store.connection() as c:return self.store.discount_quote(c,'5',code)
    def test_server_quote_normalization_rounding_and_no_discount(self):
        self.assertEqual(self.quote(' bienvenida20 ')['price'],23192)
        self.assertEqual(self.quote('')['price'],28990)
        self.save(code='UNO',percent=1)
        with self.store.connection() as c:self.assertEqual(self.store.discount_quote(c,'1','UNO')['price'],5930)
    def test_only_owner_and_valid_percent_and_code(self):
        with self.assertRaises(Problem):self.store.save_discount(self.uid,{'code':'FORGED','percent':20})
        for changes in ({'percent':0},{'percent':100},{'percent':True},{'percent':1.5},{'code':'x'},{'code':'<script>'},{'active':'true'}):
            with self.subTest(changes=changes),self.assertRaises(Problem):self.save(**changes)
    def test_invalid_or_disabled_code_cannot_purchase(self):
        self.save(active=False)
        for code in ('NOEXISTE','BIENVENIDA20'):
            with self.assertRaises(Problem):self.quote(code)
            with self.assertRaises(Problem):self.store.buy(self.uid,{'catalog_id':'5','discount_code':code,'rules_acceptance':self.rules})
        self.assertEqual(self.gateway.create_calls,0)
    def test_simulated_price_snapshot_and_forged_amount_ignored(self):
        pid=self.store.buy(self.uid,{'catalog_id':'5','discount_code':'BIENVENIDA20','price':1,'percent':99,'rules_acceptance':self.rules})
        self.save(percent=50)
        with self.store.connection() as c:
            p=c.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
            self.assertEqual((p['price'],p['discount_percent'],p['original_price']),(23192,20,28990))
    def test_changed_price_requires_fresh_confirmation(self):
        with self.assertRaises(Problem):self.store.buy(self.uid,{'catalog_id':'5','discount_code':'BIENVENIDA20','expected_price':1,'rules_acceptance':self.rules})
        with self.assertRaises(PaymentError):self.pay.create(self.uid,'5','discount-price-changed-123',self.rules,'BIENVENIDA20',1)
        self.assertEqual(self.gateway.create_calls,0)

    def test_discount_never_bypasses_rules(self):
        with self.assertRaises(Problem):self.store.buy(self.uid,{'catalog_id':'5','discount_code':'BIENVENIDA20'})
        with self.assertRaises(PaymentError):self.pay.create(self.uid,'5','discount-no-rules-12345',discount_code='BIENVENIDA20')
    def test_gateway_charges_discounted_total_and_credits_once(self):
        checkout=self.pay.create(self.uid,'5','discount-bank-key-12345',self.rules,'BIENVENIDA20')
        self.assertEqual(self.gateway.transactions[checkout['token']]['amount'],23192)
        self.pay.returned({'token_ws':checkout['token']});self.pay.returned({'token_ws':checkout['token']})
        with self.store.connection() as c:
            p=c.execute('SELECT * FROM packages WHERE user_id=?',(self.uid,)).fetchone()
            self.assertEqual((p['remaining'],p['price'],p['discount_code']),(5,23192,'BIENVENIDA20'))
    def test_retries_preserve_snapshot_after_code_disabled_and_reject_changed_code(self):
        first=self.pay.create(self.uid,'5','discount-bank-key-12345',self.rules,'BIENVENIDA20');self.save(active=False)
        self.assertEqual(self.pay.create(self.uid,'5','discount-bank-key-12345',self.rules,'BIENVENIDA20')['token'],first['token'])
        self.assertEqual(self.pay.create(self.uid,'5','resume-new-request-123456',self.rules,'BIENVENIDA20',23192,resume_payment_id=first['payment_id'])['token'],first['token'])
        with self.assertRaises(PaymentError):self.pay.create(self.uid,'5','discount-bank-key-12345',self.rules,'')
        self.save(percent=30)
        with self.assertRaises(PaymentError):self.pay.create(self.uid,'5','discount-another-key-12345',self.rules,'BIENVENIDA20',23192)
        self.assertEqual(self.gateway.create_calls,1)
