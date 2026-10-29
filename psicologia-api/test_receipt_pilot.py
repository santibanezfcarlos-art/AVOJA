import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

import pilot_app


PDF=b'%PDF-1.4\\n'+b'x'*120+b'\\n%%EOF\\n'
ORDER='PSCP'+'A'*18
SECRET='test-shared-api-secret-long-enough-for-hmac-valid'


class ReceiptMailTests(unittest.TestCase):
    def setUp(self):
        self.client=pilot_app.app.test_client()
        self.env=patch.dict(os.environ, {
            'PSICOLOGIA_PRODUCTION_FULFILLMENT_ENABLED':'true',
            'PSICOLOGIA_PUBLIC_DEMO_BRIDGE_ENABLED':'true',
            'PSICOLOGIA_DEMO_BRIDGE_KEY':SECRET,
            'PSICOLOGIA_PILOT_RESEND_API_KEY':'fake-resend-no-send',
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def data(self):
        return {'channel':'production','order':ORDER,'folio':'615',
                'mail':{
                    'from':'Ps. Carlos Santibáñez F. <contacto@pscsantibanez.cl>',
                    'to':['receptor@example.invalid'],
                    'subject':'Tu Boleta Electrónica | PSC Santibáñez',
                    'html':'<html>Recibo emitido</html>',
                    'text':'Recibo emitido',
                    'attachments':[{'filename':'boleta-615.pdf',
                        'content':base64.b64encode(PDF).decode(),
                        'content_type':'application/pdf'}],
                }}

    def signed(self,payload):
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
        stamp=str(int(time.time()))
        signature=hmac.new(SECRET.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return raw,{'Content-Type':'application/json','X-Psc-Timestamp':stamp,
                    'X-Psc-Signature':signature}

    def test_receipt_uses_existing_corporate_transport(self):
        data=self.data()
        raw,headers=self.signed(data)
        with (patch('public_demo_bridge._enabled',return_value=True),
              patch('production_fulfillment.send_payload',return_value='resend-fixture-001') as send):
            result=self.client.post('/internal/psicologia/production/receipt-mail',
                                    data=raw,headers=headers)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json['reference'],'resend-fixture-001')
        self.assertEqual(send.call_count,1)
        self.assertTrue(send.call_args.args[1].startswith('psc-receipt-'))

    def test_invalid_pdf_and_unsigned_requests_blocked(self):
        data=self.data();data['mail']['attachments'][0]['content']=base64.b64encode(b'bad').decode()
        raw,headers=self.signed(data)
        with patch('public_demo_bridge._enabled',return_value=True):
            self.assertEqual(self.client.post('/internal/psicologia/production/receipt-mail',
                                             data=raw,headers=headers).status_code,400)
            self.assertEqual(self.client.post('/internal/psicologia/production/receipt-mail',
                                             json=data).status_code,403)

if __name__=='__main__':unittest.main()
