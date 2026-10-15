import tempfile
import unittest
from pathlib import Path

from app import ADMIN_EMAIL, LEGACY_DEMO_EMAILS, Problem, Store, password_hash


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'access.sqlite3')
        self.private = 'OnlyForIsolatedTests2026!'

    def test_no_default_admin_or_seeded_professional(self):
        self.store.configure_access()
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
        for email in (*LEGACY_DEMO_EMAILS, ADMIN_EMAIL):
            with self.subTest(email=email), self.assertRaises(Problem):
                self.store.login({'email': email, 'password': 'ValhallaAdmin2026!'})

    def test_admin_mail_cannot_be_claimed_by_public_registration(self):
        for email in (ADMIN_EMAIL, ADMIN_EMAIL.upper(), *LEGACY_DEMO_EMAILS):
            with self.subTest(email=email), self.assertRaises(Problem):
                self.store.register({'email':email, 'name':'Impostor', 'password':self.private, 'specialty':'Prueba', 'role':'admin'})

    def test_admin_requires_private_password(self):
        self.store.configure_access(self.private)
        for password in ('', 'ValhallaAdmin2026!', 'wrong-password'):
            with self.subTest(password=password), self.assertRaises(Problem):
                self.store.login({'email':ADMIN_EMAIL, 'password':password})
        token = self.store.login({'email':ADMIN_EMAIL, 'password':self.private, 'portal':'admin'})
        self.assertEqual(self.store.session(token)['role'], 'admin')

    def test_migration_disables_known_accounts_and_revokes_sessions(self):
        encoded = password_hash('PublicLegacyPassword!')
        with self.store.connection(True) as c:
            for uid, email in enumerate((*LEGACY_DEMO_EMAILS, 'other-admin@test.cl'), 1):
                c.execute("INSERT INTO users(id,name,email,password,role) VALUES(?,?,?,?,'admin')", (uid,'Legacy',email,encoded))
                c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (f'old-{uid}',uid,'old-csrf',9999999999))
        self.store.configure_access(self.private)
        for uid, email in enumerate((*LEGACY_DEMO_EMAILS, 'other-admin@test.cl'), 1):
            with self.subTest(email=email):
                with self.assertRaises(Problem): self.store.session(f'old-{uid}')
                with self.assertRaises(Problem): self.store.login({'email':email,'password':'PublicLegacyPassword!'})

    def test_stale_owner_registration_does_not_retain_password_or_session(self):
        with self.store.connection(True) as c:
            c.execute("INSERT INTO users(id,name,email,password) VALUES(1,'Old owner',?,?)", (ADMIN_EMAIL,password_hash('PublicLegacyPassword!')))
            c.execute("INSERT INTO sessions VALUES('stale',1,'csrf',9999999999)")
        self.store.configure_access(self.private)
        with self.assertRaises(Problem): self.store.session('stale')
        with self.assertRaises(Problem): self.store.login({'email':ADMIN_EMAIL,'password':'PublicLegacyPassword!'})
        token=self.store.login({'email':ADMIN_EMAIL,'password':self.private})
        self.assertEqual(self.store.session(token)['role'],'admin')

    def test_password_rotation_revokes_old_sessions_and_is_idempotent(self):
        self.store.configure_access(self.private)
        old=self.store.login({'email':ADMIN_EMAIL,'password':self.private})
        self.store.configure_access(self.private)
        self.assertEqual(self.store.session(old)['role'],'admin')
        self.store.configure_access('DifferentPrivateTest2026!')
        with self.assertRaises(Problem): self.store.session(old)

    def test_professional_registration_has_no_privileges_or_hours(self):
        self.store.register({'email':'person@test.cl','name':'Profesional','password':self.private,'specialty':'Psicología','role':'admin','verified':1,'hours':20})
        token=self.store.login({'email':'person@test.cl','password':self.private})
        profile=self.store.state(self.store.session(token))
        self.assertEqual(profile['user']['role'],'professional')
        self.assertEqual(profile['user']['verified'],0)
        self.assertEqual(profile['packages'],[])
        with self.assertRaises(Problem): self.store.login({'email':'person@test.cl','password':self.private,'portal':'admin'})

    def test_short_bootstrap_password_is_rejected_without_changing_accounts(self):
        with self.assertRaises(ValueError): self.store.configure_access('short')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],0)
