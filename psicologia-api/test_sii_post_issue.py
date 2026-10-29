import os
import tempfile
import unittest
from unittest.mock import patch
import billing
import sii_post_issue as module

PDF=b'%PDF-1.4\n'+b'x'*120+b'\n%%EOF\n'

class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PSICOLOGIA_BILLING_DB':self.tmp.name+'/db.sqlite',
          'PSICOLOGIA_BILLING_ADMIN_PASSWORD':'test-password-only-123456', 'SII_POST_ISSUE_ENABLED':'true'})
        self.env.start()
        self.config=patch.object(billing,'configured',return_value=True);self.config.start()
        with billing.db() as con:
            con.executescript(billing.SCHEMA)
            for oid in ('order-a','order-b'):
                con.execute('''INSERT INTO billing_orders(order_id,environment,created_at,service,
                 appointment_date,appointment_time,amount,rut,name,address,email,phone,detail,payment_status,invoice_status)
                 VALUES (?,'production','2026-10-29','psicoterapia','2026-10-30','10:00',30000,
                 '11111111-1','Cliente <ejemplo>','Prueba','cliente@example.invalid','999999999','Servicio de prueba','approved','pending')''',(oid,))
    def tearDown(self):
        self.config.stop();self.env.stop();self.tmp.cleanup()
    def prepare(self):
        module.record_issued('order-a','123','2026-10-29',30000)
        module.store_pdf('order-a',PDF)
    def test_disabled_by_default(self):
        with patch.dict(os.environ,{'SII_POST_ISSUE_ENABLED':''}):
            with self.assertRaises(RuntimeError): self.prepare()
    def test_folio_survives_failed_download(self):
        module.record_issued('order-a','123','2026-10-29',30000)
        with self.assertRaises(ValueError): module.store_pdf('order-a',b'html error')
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT invoice_status FROM billing_orders WHERE order_id='order-a'").fetchone()[0],'issued')
    def test_idempotent_and_conflict(self):
        self.prepare();module.record_issued('order-a','123','2026-10-29',30000)
        with self.assertRaises(ValueError): module.record_issued('order-a','124','2026-10-29',30000)
        with self.assertRaises(Exception): module.record_issued('order-b','123','2026-10-29',30000)
    def test_two_independent_channels_and_no_duplicate(self):
        self.prepare();calls=[]
        def sender(receipt,key):calls.append(key);return 'provider-id'
        self.assertEqual(module.deliver('order-a','psc',sender),'accepted')
        self.assertEqual(module.deliver('order-a','psc',sender),'accepted')
        self.assertEqual(module.deliver('order-a','sii',sender),'accepted')
        self.assertEqual(len(calls),2);self.assertNotEqual(calls[0],calls[1])
    def test_uncertain_send_never_repeats(self):
        self.prepare();calls=[]
        def sender(receipt,key): calls.append(key);raise TimeoutError()
        self.assertEqual(module.deliver('order-a','psc',sender),'unknown')
        self.assertEqual(module.deliver('order-a','psc',sender),'unknown')
        self.assertEqual(len(calls),1)
    def test_payload_has_pdf_and_escapes_html(self):
        self.prepare()
        with billing.db() as con: row=dict(con.execute('SELECT * FROM sii_receipts').fetchone())
        payload=module.mail_payload(row)
        self.assertIn('&lt;ejemplo&gt;',payload['html'])
        self.assertEqual(payload['to'],['cliente@example.invalid'])
        self.assertEqual(payload['attachments'][0]['filename'],'boleta-123.pdf')
    def test_amount_and_type_checked(self):
        with self.assertRaises(ValueError):module.record_issued('order-a','123','2026-10-29',10)
        with self.assertRaises(ValueError):module.record_issued('order-a','123','2026-10-29',30000,dte=39)
    def test_private_preview_is_read_only(self):
        from flask import Flask
        from sii_visual_admin import bp
        app=Flask(__name__);app.register_blueprint(bp);client=app.test_client()
        self.assertEqual(client.get('/admin/sii-visual/post-emission/email').status_code,401)
        with patch.dict(os.environ,{'PSICOLOGIA_BILLING_ADMIN_HASH':'','PSICOLOGIA_BILLING_ADMIN_USER':'carlos'}):
            response=client.get('/admin/sii-visual/post-emission/email',auth=('carlos','test-password-only-123456'))
            self.assertEqual(response.status_code,200)
            self.assertIn('VISTA PREVIA',response.text)
            self.assertIn('no-store',response.headers['Cache-Control'])
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM billing_orders WHERE invoice_status='pending'").fetchone()[0],2)

if __name__=='__main__':unittest.main()
