import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store, Problem, ADMIN_EMAIL


class PasswordReset(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'reset.sqlite3')
        self.credentials = {'name':'Persona Prueba','email':'persona@example.cl','password':'OldPassword2026!','specialty':'Psicología'}
        self.uid = self.store.register(self.credentials)
        self.body = {'password':'NewPassword2026!','password_confirmation':'NewPassword2026!'}

    def test_single_use_hashed_token_invalidates_all_sessions_without_confirming_email(self):
        session = self.store.login(self.credentials)
        other = self.store.login(self.credentials)
        token,email = self.store.issue_password_reset(' PERSONA@example.cl ')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT token_hash FROM password_resets').fetchone()[0],hashlib.sha256(token.encode()).hexdigest())
        self.store.reset_password(dict(self.body,token=token))
        for value in (session,other):
            with self.assertRaises(Problem): self.store.session(value)
        with self.assertRaises(Problem): self.store.login(self.credentials)
        with self.assertRaises(Problem): self.store.reset_password(dict(self.body,token=token))
        new_session = self.store.login(dict(self.credentials,password=self.body['password']))
        self.assertEqual(self.store.session(new_session)['email_verified'],0)

    def test_mismatch_short_password_and_bad_token_do_not_consume_valid_token(self):
        token,_ = self.store.issue_password_reset(self.credentials['email'])
        for changes in ({'password_confirmation':'different'},{'password':'short','password_confirmation':'short'},{'token':'x'*32}):
            with self.assertRaises(Problem): self.store.reset_password(dict(self.body,token=token) | changes)
        self.store.reset_password(dict(self.body,token=token))

    def test_expired_token_resend_cooldown_and_replacement(self):
        with patch('app.time.time',return_value=10000):
            token,_ = self.store.issue_password_reset(self.credentials['email'])
            self.assertIsNone(self.store.issue_password_reset(self.credentials['email']))
        with patch('app.time.time',return_value=10061):
            replacement,_ = self.store.issue_password_reset(self.credentials['email'])
            with self.assertRaises(Problem): self.store.reset_password(dict(self.body,token=token))
        with patch('app.time.time',return_value=11862):
            with self.assertRaises(Problem): self.store.reset_password(dict(self.body,token=replacement))

    def test_unknown_inactive_and_admin_do_not_receive_tokens(self):
        self.store.configure_access('PrivateAdminPassword2026!')
        self.assertIsNone(self.store.issue_password_reset(ADMIN_EMAIL))
        self.assertIsNone(self.store.issue_password_reset('unknown@example.cl'))
        with self.store.connection(True) as c:
            c.execute('UPDATE users SET active=0 WHERE id=?',(self.uid,))
        self.assertIsNone(self.store.issue_password_reset(self.credentials['email']))

    def test_deactivation_after_request_prevents_reset(self):
        token,_ = self.store.issue_password_reset(self.credentials['email'])
        with self.store.connection(True) as c:
            c.execute('UPDATE users SET active=0 WHERE id=?',(self.uid,))
        with self.assertRaises(Problem): self.store.reset_password(dict(self.body,token=token))
