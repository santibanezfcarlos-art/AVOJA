import json
import tempfile
import threading
import unittest
import http.client
from pathlib import Path
from http.server import ThreadingHTTPServer
from app import Store, Handler, ADMIN_EMAIL
from payments import PaymentService, WebpayGateway, PaymentError


class WebpaySettings(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'settings.sqlite3');self.store.configure_access('PrivateTestPassword2026!')
        self.admin_token=self.store.login({'email':ADMIN_EMAIL,'password':'PrivateTestPassword2026!'})
        self.admin=self.store.session(self.admin_token)
        self.uid=self.store.register({'name':'Settings Test','email':'settings@example.com','password':'ProfessionalTest2026!','specialty':'Psicología'})
        self.pro_token=self.store.login({'email':'settings@example.com','password':'ProfessionalTest2026!'})
        self.service=PaymentService(self.store,WebpayGateway('disabled'))
    def test_save_restore_only_nonsecret_configuration_and_public_test_credentials(self):
        result=self.service.configure(self.admin['id'],{'commerce_code':'597012345678','sandbox_enabled':True})
        self.assertEqual(result['mode'],'integration');self.assertFalse(result['production_available'])
        self.assertEqual(self.service.gateway.commerce_code,WebpayGateway.commerce_code)
        self.assertNotEqual(self.service.gateway.commerce_code,result['commerce_code'])
        restored=PaymentService(self.store,WebpayGateway('disabled'));restored.restore_configuration()
        self.assertEqual(restored.mode,'integration');self.assertEqual(restored.configuration()['commerce_code'],'597012345678')
        restored.configure(self.admin['id'],{'commerce_code':'597012345678','sandbox_enabled':False})
        self.assertEqual(restored.mode,'disabled')
        with self.assertRaises(PaymentError):restored.create(self.uid,'5','idempotency-settings-123')
    def test_professional_cannot_configure_and_secret_or_production_rejected(self):
        with self.assertRaises(PaymentError) as error:self.service.configure(self.uid,{'commerce_code':'','sandbox_enabled':False})
        self.assertEqual(error.exception.status,403)
        for extra in ({'api_key':'private'},{'production':True},{'mode':'production'},{'secret':'private'}):
            with self.assertRaises(PaymentError):self.service.configure(self.admin['id'],{'commerce_code':'','sandbox_enabled':True,**extra})
        self.assertEqual(self.service.mode,'disabled')
    def test_eight_digit_code_saved_unchanged(self):
        result=self.service.configure(self.admin['id'],{'commerce_code':'52122833','sandbox_enabled':False})
        self.assertEqual(result['commerce_code'],'52122833')
        self.assertEqual(self.service.configuration()['commerce_code'],'52122833')

    def test_invalid_code_and_nonboolean_switch_rejected(self):
        for code in ('1234567','bad','1234567890123',True):
            with self.assertRaises(PaymentError):self.service.configure(self.admin['id'],{'commerce_code':code,'sandbox_enabled':False})
        with self.assertRaises(PaymentError):self.service.configure(self.admin['id'],{'commerce_code':'','sandbox_enabled':'true'})
    def test_pending_payment_blocks_mode_change_but_allows_code_correction(self):
        from test_migration_payments import FakeGateway
        self.service=PaymentService(self.store,FakeGateway())
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.service.create(self.uid,'5','settings-test-payment-123')
        with self.assertRaises(PaymentError) as error:self.service.configure(self.admin['id'],{'commerce_code':'','sandbox_enabled':False})
        self.assertEqual(error.exception.status,409);self.assertEqual(self.service.mode,'integration')
    def test_http_admin_csrf_and_private_metadata(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store;server.webpay=self.service
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,token='',csrf=''):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            connection.request('GET' if body is None else 'POST',path,body=None if body is None else json.dumps(body),headers={'Cookie':'session='+token,'Content-Type':'application/json','X-CSRF-Token':csrf})
            response=connection.getresponse();raw=response.read();result=(response.status,json.loads(raw));connection.close();return result
        body={'commerce_code':'597012345678','sandbox_enabled':True}
        try:
            self.assertEqual(request('/api/admin/webpay/settings',body)[0],401)
            self.assertEqual(request('/api/admin/webpay/settings',body,self.pro_token,self.store.session(self.pro_token)['csrf'])[0],403)
            self.assertEqual(request('/api/admin/webpay/settings',body,self.admin_token)[0],403)
            self.assertEqual(request('/api/admin/webpay/settings',body,self.admin_token,self.admin['csrf'])[0],200)
            status,data=request('/api/admin',token=self.admin_token)
            self.assertEqual(status,200);self.assertEqual(data['webpay_config']['commerce_code'],'597012345678')
            self.assertNotIn(WebpayGateway.api_key,json.dumps(data['webpay_config']))
            status,data=request('/api/me',token=self.pro_token)
            self.assertEqual(status,200);self.assertEqual(data['webpay_mode'],'integration');self.assertNotIn('webpay_config',data)
        finally:server.shutdown();server.server_close();thread.join()
