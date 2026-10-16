import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch,MagicMock
from app import Store,Handler,ADMIN_EMAIL
from mailer import EmailDelivery

class PrivateMailDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'diagnostics.sqlite3');self.store.configure_access('AdminPasswordTest2026!')
        self.admin_token=self.store.login({'email':ADMIN_EMAIL,'password':'AdminPasswordTest2026!'})
        self.admin_csrf=self.store.session(self.admin_token)['csrf']
        self.uid=self.store.register({'name':'Mail Test','email':'mail@example.cl','password':'ProfessionalPassword2026!','specialty':'Psicología'})
        self.token=self.store.login({'email':'mail@example.cl','password':'ProfessionalPassword2026!'})
        self.csrf=self.store.session(self.token)['csrf']
        with patch.dict(os.environ,{'VALHALLA_EMAIL_PROVIDER':'resend','VALHALLA_RESEND_API_KEY':'private-test-secret','VALHALLA_EMAIL_FROM':'notificaciones@valhallaoffice.cl','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'},clear=True):delivery=EmailDelivery()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=self.store;self.server.email_delivery=delivery
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,token='',csrf=''):
        headers={'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf}
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:response=urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:response=error
        return response.status,json.load(response)
    def accepted(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"id":"test-only-id"}';return response
    def test_test_endpoint_is_admin_csrf_protected_and_recipient_cannot_be_overridden(self):
        self.assertEqual(self.request('/api/admin/email/test',{})[0],401)
        self.assertEqual(self.request('/api/admin/email/test',{},self.token,self.csrf)[0],403)
        self.assertEqual(self.request('/api/admin/email/test',{},self.admin_token)[0],403)
        with patch('mailer.urlopen',return_value=self.accepted()) as send:
            status,result=self.request('/api/admin/email/test',{'email':'arbitrary@example.cl'},self.admin_token,self.admin_csrf)
            self.assertEqual(status,200);self.assertTrue(result['ok'])
            self.assertEqual(json.loads(send.call_args.args[0].data)['to'],[ADMIN_EMAIL])
            self.assertEqual(self.request('/api/admin/email/test',{},self.admin_token,self.admin_csrf)[0],429)
            self.assertEqual(send.call_count,1)
        self.assertEqual(self.store.session(self.admin_token)['role'],'admin')
        status,data=self.request('/api/admin',token=self.admin_token)
        self.assertEqual(status,200);self.assertEqual(data['mail_events'][0]['status'],'accepted')
        self.assertNotIn('private-test-secret',json.dumps(data))
        self.assertEqual(self.request('/api/admin',token=self.token)[0],403)
    def test_provider_rejection_is_visible_only_to_admin_and_no_response_body_is_retained(self):
        error=urllib.error.HTTPError('https://api.resend.com/emails',401,'secret response',{},io.BytesIO(b'private-test-secret reset-token'))
        with patch('mailer.urlopen',side_effect=error):
            status,data=self.request('/api/admin/email/test',{},self.admin_token,self.admin_csrf)
        self.assertEqual(status,200);self.assertFalse(data['ok'])
        events=self.request('/api/admin',token=self.admin_token)[1]['mail_events']
        self.assertEqual(events[0]['category'],'invalid_key');self.assertEqual(events[0]['http_status'],401)
        for value in ('private-test-secret','reset-token','secret response'):self.assertNotIn(value,json.dumps(events))
    def test_failed_confirmation_does_not_claim_a_message_was_sent(self):
        with patch('mailer.urlopen',side_effect=urllib.error.URLError('private details')):
            status,result=self.request('/api/email-verification/resend',{},self.token,self.csrf)
        self.assertEqual(status,200);self.assertFalse(result['sent'])
        profile=self.request('/api/me',token=self.token)[1]
        self.assertEqual(profile['email_verification_delivery'],'error');self.assertEqual(profile['user']['email_verified'],0)
        self.assertNotIn('network_error',json.dumps(profile))
    def test_recovery_remains_generic_for_missing_account_and_provider_failure(self):
        with patch('mailer.urlopen',side_effect=urllib.error.URLError('private details')):
            status,known=self.request('/api/password-reset/request',{'email':'mail@example.cl'})
            other_status,unknown=self.request('/api/password-reset/request',{'email':'unknown@example.cl'})
        self.assertEqual(status,200);self.assertEqual(other_status,200);self.assertEqual(known,unknown)
        events=self.request('/api/admin',token=self.admin_token)[1]['mail_events']
        self.assertEqual(events[0]['category'],'account_or_limit');self.assertEqual(events[1]['category'],'network_error')
