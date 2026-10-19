from rules import RULES_VERSION
import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from app import ADMIN_EMAIL, CATALOG, CHILE, Handler, Problem, Store, stamp


class IdentityAndSchedule(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'data.sqlite3'
        self.store = Store(self.path)
        self.clock = datetime(2026, 10, 15, 8, tzinfo=CHILE)
        self.clock_patch = patch('app.now', side_effect=lambda: self.clock)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.password = 'IsolatedScheduleTest2026!'
        self.store.configure_access(self.password)
        self.admin_token = self.store.login({'email': ADMIN_EMAIL, 'password': self.password})
        self.owner = self.store.session(self.admin_token)['id']
        self.store.register({'name': 'Profesional Agenda', 'email': 'agenda@test.cl', 'password': self.password, 'specialty': 'Psicología'})
        self.pro_token = self.store.login({'email': 'agenda@test.cl', 'password': self.password})
        self.uid = self.store.session(self.pro_token)['id']
        email_token,_ = self.store.issue_email_token(self.uid)
        self.store.confirm_email(email_token)
        with self.store.connection(True) as c:
            for uid in (self.uid,self.owner):
                c.execute("INSERT INTO documents(user_id,filename,mime,content,status,created_at,kind) VALUES(?,'degree.pdf','application/pdf',?,'approved',?,'degree')",(uid,b'Degree fixture',stamp(self.clock)))

    def slot(self, hour=10, day=1):
        return {'box': 1, 'start': stamp((self.clock + timedelta(days=day)).replace(hour=hour, minute=0, second=0, microsecond=0))}

    def pay(self, uid=None, catalog='5'):
        pid = self.store.buy(uid or self.uid, {'rules_acceptance':{'accepted':True,'version':RULES_VERSION},'catalog_id': catalog})
        self.store.admin_action(self.owner, {'action': 'payment', 'id': pid, 'decision': 'paid'})
        return pid

    def upload(self, kind='identity', uid=None):
        uid = uid or self.uid
        self.store.upload(uid, {'kind': kind, 'filename': 'ficticio.pdf', 'content': base64.b64encode(b'%PDF-1.4\nFICTICIO').decode()})
        with self.store.connection() as c:
            return c.execute('SELECT MAX(id) FROM documents WHERE user_id=?', (uid,)).fetchone()[0]

    def approve(self, did):
        self.store.admin_action(self.owner, {'action': 'document', 'id': did, 'decision': 'approved', 'identity_confirmed': True})

    def test_payment_and_forged_verified_flag_do_not_replace_identity(self):
        self.pay()
        with self.store.connection(True) as c:
            c.execute('UPDATE users SET verified=1 WHERE id=?', (self.uid,))
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot())
        self.assertEqual(self.store.state(self.store.session(self.pro_token))['user']['verified'], 0)

    def test_accreditation_is_not_identity(self):
        self.approve(self.upload('accreditation'))
        self.pay()
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot())

    def test_paid_and_approved_identity_are_independently_required(self):
        self.approve(self.upload())
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot())
        self.pay()
        self.store.reserve(self.uid, self.slot())

    def test_owner_professional_profile_also_requires_identity(self):
        self.pay(self.owner)
        with self.assertRaises(Problem): self.store.reserve(self.owner, self.slot())
        self.approve(self.upload(uid=self.owner))
        self.store.reserve(self.owner, self.slot())

    def test_identity_approval_requires_explicit_both_sides_confirmation(self):
        did = self.upload()
        for confirmation in (None, False, 'true'):
            with self.subTest(confirmation=confirmation), self.assertRaises(Problem):
                self.store.admin_action(self.owner, {'action': 'document', 'id': did, 'decision': 'approved', 'identity_confirmed': confirmation})
        self.approve(did)

    def test_new_identity_blocks_booking_and_modification_until_approved(self):
        self.pay()
        self.approve(self.upload())
        bid = self.store.reserve(self.uid, self.slot())
        did = self.upload()
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot(11))
        with self.assertRaises(Problem): self.store.modify(self.uid, bid, self.slot(11))
        self.store.admin_action(self.owner, {'action': 'document', 'id': did, 'decision': 'rejected', 'note': 'Falta el reverso'})
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot(11))

    def test_new_accreditation_does_not_invalidate_approved_identity(self):
        self.approve(self.upload())
        self.upload('accreditation')
        self.pay()
        self.store.reserve(self.uid, self.slot())

    def test_name_change_requires_identity_review_again(self):
        self.approve(self.upload()); self.pay()
        self.store.update_profile(self.uid, {'name':'Nombre cambiado','specialty':'Psicología'})
        with self.assertRaises(Problem): self.store.reserve(self.uid, self.slot())
        with self.store.connection() as c:
            self.assertEqual(c.execute("SELECT status FROM documents WHERE user_id=? AND kind='identity'", (self.uid,)).fetchone()[0], 'pending')

    def test_old_database_documents_are_migrated_as_accreditation(self):
        self.approve(self.upload())
        with self.store.connection(True) as c:
            c.execute('ALTER TABLE documents DROP COLUMN kind')
        restored = Store(self.path)
        restored.configure_access(self.password)
        with restored.connection() as c:
            self.assertEqual(c.execute('SELECT kind FROM documents').fetchone()[0], 'accreditation')
        self.pay()
        with self.assertRaises(Problem): restored.reserve(self.uid, self.slot())

    def test_catalog_matches_requested_prices(self):
        self.assertEqual(CATALOG, {'1': (1,5990), '5': (5,28990), '10': (10,54990), '30': (30,144990), '60': (60,254990), '100': (100,364990), '200': (200,599990)})
        for key, (hours, price) in CATALOG.items():
            pid = self.store.buy(self.uid, {'rules_acceptance':{'accepted':True,'version':RULES_VERSION},'catalog_id': key, 'price': 1, 'hours': 999})
            with self.store.connection() as c:
                row = c.execute('SELECT * FROM packages WHERE id=?', (pid,)).fetchone()
                self.assertEqual((row['hours'], row['price']), (hours, price))

    def test_sessions_last_55_minutes_with_hourly_starts(self):
        self.approve(self.upload()); self.pay()
        first = self.slot(10)
        self.store.reserve(self.uid, first)
        profile = self.store.state(self.store.session(self.pro_token))
        booking = profile['bookings'][0]
        self.assertEqual(datetime.fromisoformat(booking['end']) - datetime.fromisoformat(booking['start']), timedelta(minutes=55))
        with self.assertRaises(Problem):
            self.store.reserve(self.uid, {**first, 'start': stamp(datetime.fromisoformat(first['start']) + timedelta(minutes=55))})
        self.store.reserve(self.uid, self.slot(11))
        self.assertEqual(self.store.state(self.store.session(self.pro_token))['packages'][0]['remaining'], 3)

    def test_admin_timing_distinguishes_session_and_five_minute_buffer(self):
        self.approve(self.upload()); self.pay()
        self.store.reserve(self.uid, self.slot(9, day=0))
        for hour, minute, expected in ((9,0,'in_session'), (9,54,'in_session'), (9,55,'buffer'), (9,59,'buffer'), (10,0,'finished')):
            self.clock = self.clock.replace(hour=hour, minute=minute)
            data = self.store.availability(self.clock.date().isoformat(), include_names=True)
            booking = data['slots'][1]['boxes'][0]['booking']
            self.assertEqual(booking['timing'], expected)
            self.assertEqual(booking['name'], 'Profesional Agenda')

    def test_only_admin_endpoint_exposes_booking_names(self):
        self.approve(self.upload()); self.pay(); self.store.reserve(self.uid, self.slot())
        class QuietHandler(Handler):
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        server.store = self.store
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        date = (self.clock + timedelta(days=1)).date().isoformat()
        def request(path, token):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path, headers={'Cookie': 'session=' + token})
            try: response = opener.open(req)
            except urllib.error.HTTPError as error: response = error
            return response.status, json.loads(response.read())
        try:
            self.assertEqual(request('/api/admin/availability?date=' + date, self.pro_token)[0], 403)
            status, public = request('/api/availability?date=' + date + '&include_names=true', self.pro_token)
            self.assertEqual(status, 200)
            self.assertNotIn('Profesional Agenda', json.dumps(public))
            self.assertNotIn('agenda@test.cl', json.dumps(public))
            status, admin = request('/api/admin/availability?date=' + date, self.admin_token)
            self.assertEqual(status, 200)
            cell = admin['slots'][2]['boxes'][0]
            self.assertEqual(cell['booking']['name'], 'Profesional Agenda')
            self.assertEqual(cell['booking']['user_id'], self.uid)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_email_is_required_even_after_payment_and_identity_approval(self):
        self.approve(self.upload())
        self.pay()
        with self.store.connection(True) as c:
            c.execute('UPDATE users SET email_verified=0 WHERE id=?',(self.uid,))
            c.execute('DELETE FROM email_verifications WHERE user_id=?',(self.uid,))
        with self.assertRaises(Problem): self.store.reserve(self.uid,self.slot())
        token,_ = self.store.issue_email_token(self.uid)
        self.store.confirm_email(token)
        self.store.reserve(self.uid,self.slot())
