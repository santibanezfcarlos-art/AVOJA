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
