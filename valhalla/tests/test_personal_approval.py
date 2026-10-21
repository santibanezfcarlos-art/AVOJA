import base64
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from app import Store,Problem,ADMIN_EMAIL,now,stamp,CHILE
from rules import RULES_VERSION

class PersonalApproval(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'personal.sqlite3');self.store.configure_access('PersonalAdmin2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'PersonalAdmin2026!'}))['id']
        self.credentials={'name':'Colega Conocido','email':'personal@example.cl','specialty':'Psicología','password':'PersonalPro2026!'}
        self.uid=self.store.register(self.credentials);self.token=self.store.login(self.credentials)
        self.body={'action':'personal_approval','id':self.uid,'decision':'approved','confirmed':True}
    def state(self):return self.store.state(self.store.session(self.token))
    def approve(self):self.store.admin_action(self.admin,self.body)
    def slot(self):return {'box':1,'start':stamp((now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0))}
    def test_personal_approval_is_separate_and_idempotent(self):
        self.approve();self.approve();state=self.state()
        self.assertEqual(state['user']['identity_source'],'personal');self.assertEqual(state['user']['verified'],1)
        self.assertFalse(state['user']['degree_approved']);self.assertEqual(state['documents'],[]);self.assertEqual(state['packages'],[])
        admin=self.store.admin_state();self.assertEqual(len(admin['personal_approvals']),1)
        self.assertEqual(admin['personal_approvals'][0]['actor_id'],self.admin)
        self.assertTrue(next(u for u in admin['users'] if u['id']==self.uid)['personally_approved'])
    def test_admin_confirmation_and_active_account_required(self):
        with self.assertRaises(Problem):self.store.admin_action(self.uid,self.body)
        for changes in ({'confirmed':False},{'decision':'unknown'},{'id':self.admin}):
            with self.subTest(changes=changes),self.assertRaises(Problem):self.store.admin_action(self.admin,self.body|changes)
        with self.store.connection(True) as c:c.execute('UPDATE users SET active=0 WHERE id=?',(self.uid,))
        with self.assertRaises(Problem):self.approve()
    def test_email_balance_and_rules_remain_required_and_revocation_preserves_reservation(self):
        self.approve()
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot())
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot())
        pid=self.store.manual_credit(self.admin,{'user_id':self.uid,'hours':3,'amount':0,'method':'courtesy','confirmed':True,'request_key':'personal-courtesy-123456'})['package_id']
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot())
        self.store.accept_manual_rules(self.uid,{'package_id':pid,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        self.store.reserve(self.uid,self.slot())
        self.store.admin_action(self.admin,self.body|{'decision':'revoked'})
        state=self.state();self.assertFalse(state['user']['verified']);self.assertEqual(state['packages'][0]['remaining'],2);self.assertEqual(state['bookings'][0]['status'],'confirmed')
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot()|{'box':2})
    def test_name_change_requires_new_personal_approval(self):
        self.approve();self.store.update_profile(self.uid,{'name':'Nombre cambiado','specialty':'Psicología'})
        self.assertFalse(self.state()['user']['verified']);self.approve();self.assertEqual(len(self.store.admin_state()['personal_approvals']),2)
    def test_new_identity_invalidates_exception_without_forging_document_approval(self):
        self.approve();self.store.upload(self.uid,{'kind':'identity','filename':'new.pdf','content':base64.b64encode(b'fixture').decode()})
        self.assertFalse(self.state()['user']['verified']);self.assertEqual(self.state()['documents'][0]['status'],'pending')
