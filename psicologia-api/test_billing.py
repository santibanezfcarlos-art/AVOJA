"""Offline integration checks: never contacts Transbank or uses real patient data."""
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

import billing
import main
import webpay_demo as webpay

class BillingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'PSICOLOGIA_BILLING_DB': self.tmp.name + '/billing.sqlite3',
            'PSICOLOGIA_BILLING_ADMIN_PASSWORD': 'offline-test-password-only',
            'PSICOLOGIA_BILLING_ADMIN_HASH': '', 'RENDER': '',
            'PSICOLOGIA_WEBPAY_MODE': 'integration',
            'PSICOLOGIA_WEBPAY_COMMERCE_CODE': webpay.TEST_COMMERCE,
            'PSICOLOGIA_WEBPAY_API_KEY': 'offline-fixture',
            'PSICOLOGIA_WEBPAY_DEMO_SIGNING_KEY': 'offline-fixture-' * 4})
        self.env.start()
        self.client = main.app.test_client()
        self.auth = ('carlos', 'offline-test-password-only')
        self.form = dict(service='psicoterapia', billing_required='1', billing_rut='12.345.678-5',
                         billing_name='Persona de prueba', billing_address='Dirección ficticia 123',
                         billing_email='prueba@example.invalid', billing_phone='+56912345678')
        self.person = billing.receiver(self.form)
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def order(self, order='test-order', environment='integration'):
        billing.prepare(order, 'psicoterapia', '', '', 30000, self.person, environment)
        billing.bind_token(order, order * 3)
        return order * 3
    def test_auth_and_cors(self):
        for route in ['/admin/boletas', '/admin/boletas/data', '/admin/boletas/export.csv']:
            self.assertEqual(self.client.get(route).status_code, 401)
            response = self.client.get(route, auth=self.auth, headers={'Origin':webpay.SITE})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('Access-Control-Allow-Origin', response.headers)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
    def test_validation(self):
        for key,value in [('billing_rut','12345678-9'),('billing_name',''),('billing_address','x'),
                          ('billing_email','bad'),('billing_phone','abc')]:
            with self.assertRaises(ValueError):
                billing.receiver({**self.form, key:value})
    def test_payment_flow(self):
        token = 'a' * 64
        sent = {}
        def send(method,url,data=None):
            if method == 'POST':
                sent.update(data)
                return {'token':token,'url':webpay.CHECKOUT_URL}
            return {'session_id':sent['session_id'],'buy_order':sent['buy_order'],
                    'amount':30000,'status':'AUTHORIZED','response_code':0,'payment_type_code':'VD'}
        with patch.object(webpay, '_send', side_effect=send):
            r = self.client.post('/api/webpay-demo/start',data=self.form,headers={'Origin':webpay.SITE})
            self.assertEqual(r.status_code,200)
            self.assertNotIn(self.person['email'],str(sent))
            r = self.client.post('/api/webpay-demo/return',data={'token_ws':token})
            self.assertEqual(r.status_code,303)
            self.assertNotIn(self.person['rut'],r.location)
        rows = self.client.get('/admin/boletas/data?environment=integration&status=test',auth=self.auth).json['orders']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['name'], self.person['name'])
        self.assertEqual(rows[0]['invoice_status'],'test')
    def mark(self, order, folio='123'):
        return self.client.post('/admin/boletas/'+order+'/issued', auth=self.auth,
                    headers={'X-Billing-Action':'manual-invoice'}, json={'folio':folio,
                    'issued_date':datetime.now(ZoneInfo('America/Santiago')).date().isoformat()})
    def test_test_invoice_blocked(self):
        token=self.order()
        billing.record_result('test-order',token,30000,True,'VD')
        self.assertEqual(self.mark('test-order').status_code,409)
    def test_production_marking_idempotence_and_duplicate_folio(self):
        for order in ['one','two']:
            token=self.order(order,'production')
            billing.record_result(order,token,30000,True,'VD')
        self.assertEqual(self.mark('one').status_code,200)
        self.assertEqual(self.mark('one').status_code,200)
        self.assertEqual(self.mark('two').status_code,409)
        billing.record_result('one','one'*3,30000,False,'VD')
        with billing.db() as con:
            row=con.execute("SELECT * FROM billing_orders WHERE order_id='one'").fetchone()
            self.assertEqual(row['invoice_status'],'issued')
            self.assertEqual(row['payment_status'],'approved')
            self.assertEqual(con.execute('SELECT count(*) FROM billing_audit').fetchone()[0],1)
    def test_rejected_cancelled_and_mismatch(self):
        token=self.order()
        with self.assertRaises(ValueError):
            billing.record_result('test-order',token,1,True,'VD')
        billing.cancelled(token)
        with billing.db() as con:
            self.assertEqual(con.execute('SELECT payment_status FROM billing_orders').fetchone()[0],'cancelled')
        billing.record_result('test-order',token,30000,False,'VD')
        self.assertEqual(self.mark('test-order').status_code,409)
    def test_csrf(self):
        self.assertEqual(self.client.post('/admin/boletas/one/issued',auth=self.auth,json={}).status_code,403)
    def test_unconfigured_stops_payment(self):
        with patch.dict(os.environ, {'PSICOLOGIA_BILLING_ADMIN_PASSWORD':''}):
            self.assertFalse(self.client.get('/api/billing/config').json['ready'])
            with patch.object(webpay,'_send') as send:
                self.assertEqual(self.client.post('/api/webpay-demo/start',data=self.form,headers={'Origin':webpay.SITE}).status_code,503)
                send.assert_not_called()
    def test_export_and_persistence(self):
        self.person['name']='=FORMULA()'
        self.order()
        r=self.client.get('/admin/boletas/export.csv?environment=integration&status=all',auth=self.auth)
        self.assertIn("'=FORMULA()",r.data.decode())
        self.assertIn(self.person['email'],r.data.decode())
        with billing.db() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM billing_orders').fetchone()[0],1)

if __name__ == '__main__':
    unittest.main()
