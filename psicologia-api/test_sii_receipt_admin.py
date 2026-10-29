"""Offline regression tests: no real SII issue and no external Resend calls."""
import io
import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import billing
import main

PDF=b'%PDF-1.4\\n'+b'x'*120+b'\\n%%EOF\\n'


class ReceiptAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ, {
            'RENDER':'',
            'PSICOLOGIA_BILLING_DB':self.tmp.name+'/db.sqlite',
            'PSICOLOGIA_BILLING_ADMIN_PASSWORD':'offline-private-password',
            'PSICOLOGIA_BILLING_ADMIN_HASH':'',
            'SII_POST_ISSUE_ENABLED':'true'})
        self.env.start()
        self.client=main.app.test_client()
        self.auth=('carlos','offline-private-password')
        person=dict(rut='12345678-5',name='Receptor de prueba',
                    address='Calle ficticia 123',email='receptor@example.invalid',
                    phone='+56912345678')
        billing.prepare('PSCP'+'A'*18,'psicoterapia','2026-11-12','10:00',
                        30000,person,'production')
        billing.bind_token('PSCP'+'A'*18,'X'*40)
        billing.record_result('PSCP'+'A'*18,'X'*40,30000,True,'VD')
        self.order='PSCP'+'A'*18
        self.headers={'X-Billing-Action':'manual-invoice'}
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def mark(self):
        return self.client.post('/admin/boletas/'+self.order+'/issued',
                                auth=self.auth,headers=self.headers,
                                json={'folio':'615','issued_date':datetime.now(
                                     ZoneInfo('America/Santiago')).date().isoformat()})

    def upload(self,data=PDF):
        return self.client.post('/admin/boletas/'+self.order+'/official-pdf',
                                auth=self.auth,headers=self.headers,
                                data={'file':(io.BytesIO(data),'boleta.pdf')})

    def test_pending_purchase_cannot_upload(self):
        self.assertEqual(self.upload().status_code,409)
        self.assertEqual(self.client.get('/admin/boletas/'+self.order+'/official-pdf',
                          auth=self.auth).status_code,404)

    def test_pdf_persists_downloads_and_resend_is_idempotent(self):
        self.assertEqual(self.mark().status_code,200)
        self.assertEqual(self.upload().status_code,200)
        self.assertEqual(self.upload().status_code,200)
        downloaded=self.client.get('/admin/boletas/'+self.order+'/official-pdf',auth=self.auth)
        self.assertEqual(downloaded.data,PDF)
        state=self.client.get('/admin/boletas/receipts',auth=self.auth).json['receipts'][0]
        self.assertEqual(state['folio'],'615')
        self.assertTrue(state['has_pdf'])
        calls=[]
        def mocked_remote(action,payload):
            calls.append((action,payload['order'],payload['folio'],
                          payload['mail']['attachments'][0]['filename']))
            return {'state':'SENT','reference':'resend-abc-123'}
        with patch('production_checkout.remote',side_effect=mocked_remote):
            r=self.client.post('/admin/boletas/'+self.order+'/send-psc',
                               auth=self.auth,headers=self.headers)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json['state'],'accepted')
            r2=self.client.post('/admin/boletas/'+self.order+'/send-psc',
                               auth=self.auth,headers=self.headers)
            self.assertEqual(r2.json['state'],'accepted')
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0],('receipt-mail',self.order,'615','boleta-615.pdf'))

    def test_uncertain_dispatch_is_not_repeated(self):
        self.mark();self.upload()
        calls=[]
        def error(*args):
            calls.append(1)
            raise TimeoutError()
        with patch('production_checkout.remote',side_effect=error):
            a=self.client.post('/admin/boletas/'+self.order+'/send-psc',
                               auth=self.auth,headers=self.headers)
            b=self.client.post('/admin/boletas/'+self.order+'/send-psc',
                               auth=self.auth,headers=self.headers)
        self.assertEqual(a.json['state'],'unknown')
        self.assertEqual(b.json['state'],'unknown')
        self.assertEqual(len(calls),1)

    def test_authorization_invalid_pdf_and_order_binding(self):
        self.mark()
        self.assertEqual(self.client.post('/admin/boletas/'+self.order+'/official-pdf',
                                         data={'file':(io.BytesIO(PDF),'a.pdf')}).status_code,401)
        self.assertEqual(self.client.post('/admin/boletas/'+self.order+'/official-pdf',
                                         auth=self.auth,data={'file':(io.BytesIO(PDF),'a.pdf')}).status_code,403)
        self.assertEqual(self.upload(b'not pdf').status_code,409)
        self.assertEqual(self.client.get('/admin/boletas/receipts').status_code,401)
        self.assertEqual(self.client.post('/admin/boletas/no-such-order/send-psc',
                                         auth=self.auth,headers=self.headers).status_code,409)


if __name__=='__main__':
    unittest.main()
