import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app import Store, Problem
from mailer import EmailDelivery

class EmailAndFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.uid = self.store.register({'name':'Test Professional','email':'person@example.cl','password':'LongPassword2026!','email_verified':1,'specialty':'Psicología'})

    def test_signup_cannot_forge_verification_and_token_is_hashed_single_use(self):
        token, email = self.store.issue_email_token(self.uid)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT email_verified FROM users WHERE id=?',(self.uid,)).fetchone()[0],0)
            row = c.execute('SELECT token_hash FROM email_verifications').fetchone()
            self.assertEqual(row[0],hashlib.sha256(token.encode()).hexdigest())
        self.store.confirm_email(token)
        with self.assertRaises(Problem): self.store.confirm_email(token)
        with self.assertRaises(Problem): self.store.confirm_email('x'*32)

    def test_expiry_cooldown_and_replacement(self):
        token,_ = self.store.issue_email_token(self.uid)
        with self.assertRaises(Problem): self.store.issue_email_token(self.uid)
        with self.store.connection(True) as c:
            c.execute('UPDATE email_verifications SET expires=0,created_at=0')
        with self.assertRaises(Problem): self.store.confirm_email(token)
        replacement,_ = self.store.issue_email_token(self.uid)
        with self.assertRaises(Problem): self.store.confirm_email(token)
        self.store.confirm_email(replacement)

    def test_any_extension_and_grouped_attachments(self):
        files = [{'filename':name,'content':base64.b64encode(b'private bytes').decode()} for name in ['front.heic','back.jpg','scan.docx','identity.zip','notes.html']]
        self.store.upload(self.uid,{'kind':'identity','files':files})
        with self.store.connection() as c:
            row = c.execute('SELECT id,filename FROM documents').fetchone()
            metadata = self.store.document_metadata(c,row)
            self.assertEqual(len(metadata['files']),5)
            self.assertNotIn('content',json.dumps(metadata))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM document_attachments').fetchone()[0],4)
        files[1]['content'] = 'not base64!'
        with self.assertRaises(Problem): self.store.upload(self.uid,{'files':files})
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM documents').fetchone()[0],1)
        with self.assertRaises(Problem): self.store.upload(self.uid,{'files':files*2})

class Mailer(unittest.TestCase):
    @patch.dict(os.environ,{'VALHALLA_EMAIL_PROVIDER':'sendgrid','VALHALLA_SENDGRID_API_KEY':'test-only','VALHALLA_EMAIL_FROM':'sender@gmail.com'},clear=True)
    def test_sendgrid_payload_and_empty_accepted_response(self):
        response = MagicMock()
        response.__enter__.return_value.status = 202
        with patch('mailer.urlopen',return_value=response) as send:
            EmailDelivery().send_verification('recipient@example.cl','random-token')
            request = send.call_args.args[0]
            self.assertEqual(request.full_url,'https://api.sendgrid.com/v3/mail/send')
            payload = json.loads(request.data)
            self.assertEqual(payload['from']['email'],'sender@gmail.com')
            self.assertIn('verify_email=random-token',payload['content'][0]['value'])

    @patch.dict(os.environ,{},clear=True)
    def test_unconfigured_delivery_cannot_send(self):
        delivery = EmailDelivery()
        self.assertFalse(delivery.configured)
        with patch('mailer.urlopen') as send:
            with self.assertRaises(RuntimeError): delivery.send_verification('x@example.cl','token')
            send.assert_not_called()

class ResendDelivery(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{'VALHALLA_EMAIL_PROVIDER':' resend ','VALHALLA_RESEND_API_KEY':' test-private-key ','VALHALLA_EMAIL_FROM':' Valhalla Office <notificaciones@valhallaoffice.cl> ','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'},clear=True)
        self.environment.start();self.addCleanup(self.environment.stop)
    def test_resend_verification_and_recovery_payloads_have_correct_links_and_client_header(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"id":"test-message-id"}'
        with patch('mailer.urlopen',return_value=response) as send:
            delivery=EmailDelivery();self.assertTrue(delivery.configured)
            delivery.send_verification('person@example.cl','verification-token')
            request=send.call_args.args[0];payload=json.loads(request.data)
            self.assertEqual(request.full_url,'https://api.resend.com/emails')
            self.assertEqual(request.get_header('Authorization'),'Bearer test-private-key')
            self.assertTrue(request.get_header('User-agent').startswith('ValhallaOffice/'))
            self.assertEqual(payload['from'],'Valhalla Office <notificaciones@valhallaoffice.cl>')
            self.assertIn('https://valhallaoffice.cl/?verify_email=verification-token',payload['text'])
            delivery.send_password_reset('person@example.cl','reset-token')
            self.assertIn('https://valhallaoffice.cl/?reset_password=reset-token',json.loads(send.call_args.args[0].data)['text'])
            self.assertNotIn('test-private-key',json.dumps(delivery.diagnostics()))
    def test_provider_errors_are_classified_without_exposing_secret_response(self):
        import io
        from urllib.error import HTTPError,URLError
        from mailer import EmailDeliveryError
        for status,category in ((401,'invalid_key'),(403,'permissions_or_domain'),(422,'invalid_sender_or_payload'),(429,'rate_limited'),(500,'provider_unavailable')):
            error=HTTPError('https://api.resend.com/emails',status,'private response',{},io.BytesIO(b'test-private-key reset-token person@example.cl'))
            with patch('mailer.urlopen',side_effect=error):
                with self.assertRaises(EmailDeliveryError) as raised:EmailDelivery().send_test('owner@example.cl')
                self.assertEqual(raised.exception.category,category);self.assertEqual(raised.exception.http_status,status)
                for value in ('test-private-key','reset-token','person@example.cl'):self.assertNotIn(value,str(raised.exception))
        with patch('mailer.urlopen',side_effect=URLError('secret network details')):
            with self.assertRaises(EmailDeliveryError) as raised:EmailDelivery().send_test('owner@example.cl')
            self.assertEqual(raised.exception.category,'network_error')
    def test_malformed_provider_success_is_not_reported_as_sent(self):
        from mailer import EmailDeliveryError
        for raw in (b'[]',b'{}',b'not json',b'{"id":null}'):
            response=MagicMock();response.__enter__.return_value.read.return_value=raw
            with patch('mailer.urlopen',return_value=response):
                with self.assertRaises(EmailDeliveryError):EmailDelivery().send_test('owner@example.cl')
