import base64
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from app import Store,Problem,ADMIN_EMAIL,now,stamp,CHILE
from google_auth import authenticate,GoogleError
from rules import RULES_VERSION

class UserAccess(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'access.sqlite3');self.store.configure_access('ManageUsersAdmin2026!')
        self.admin_token=self.store.login({'email':ADMIN_EMAIL,'password':'ManageUsersAdmin2026!'});self.admin=self.store.session(self.admin_token)['id']
        self.credentials={'name':'Cuenta de Prueba','email':'typo@example.cl','specialty':'Psicología','password':'ManageUsersPro2026!'}
        self.uid=self.store.register(self.credentials);self.token=self.store.login(self.credentials)
    def action(self,action,**extra):return self.store.manage_user_access(self.admin,{'action':action,'user_id':self.uid,'confirmed':True}|extra)
    def test_block_is_distinct_from_deactivation_and_reactivation_does_not_unblock(self):
        self.action('block');self.assertTrue(next(u for u in self.store.admin_state()['users'] if u['id']==self.uid)['active'])
        with self.assertRaises(Problem):self.store.session(self.token)
        with self.assertRaises(Problem):self.store.login(self.credentials)
        with self.assertRaises(Problem):self.store.register(self.credentials|{'email':' TYPO@EXAMPLE.CL '})
        self.store.admin_action(self.admin,{'action':'user','id':self.uid,'active':True})
        with self.assertRaises(Problem):self.store.login(self.credentials)
        self.assertEqual(self.store.issue_password_reset(self.credentials['email']),None)
        self.action('unblock',email=self.credentials['email']);self.store.login(self.credentials)
    def test_unblock_respects_standby_and_records_optional_reason(self):
        self.store.admin_action(self.admin,{'action':'user','id':self.uid,'active':False})
        self.action('block',note='Incumplimiento de normas');self.action('unblock',email=self.credentials['email'])
        with self.assertRaises(Problem):self.store.login(self.credentials)
        events=self.store.admin_state()['user_access_events'];self.assertEqual(len(events),2);self.assertEqual(events[-1]['note'],'Incumplimiento de normas');self.assertEqual(events[-1]['actor_id'],self.admin)
    def test_delete_erroneous_account_releases_email_but_never_reuses_id_or_documents(self):
        token,email=self.store.issue_email_token(self.uid)
        self.store.upload(self.uid,{'kind':'identity','filename':'private.pdf','content':base64.b64encode(b'fixture').decode()})
        result=self.action('delete');self.assertFalse(result['history_preserved'])
        with self.assertRaises(Problem):self.store.session(self.token)
        with self.assertRaises(Problem):self.store.confirm_email(token)
        with self.assertRaises(Problem):self.store.admin_action(self.admin,{'action':'user','id':self.uid,'active':True})
        uid2=self.store.register(self.credentials);self.assertNotEqual(self.uid,uid2)
        with self.store.connection() as c:
            old=c.execute('SELECT * FROM users WHERE id=?',(self.uid,)).fetchone();self.assertTrue(old['deleted_at']);self.assertNotEqual(old['email'],email)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM documents WHERE user_id=?',(self.uid,)).fetchone()[0],0)
        self.assertEqual(self.store.session(self.store.login(self.credentials))['verified'],0)
    def test_delete_with_activity_preserves_balance_and_reservations_without_access(self):
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.store.admin_action(self.admin,{'action':'personal_approval','id':self.uid,'decision':'approved','confirmed':True})
        pid=self.store.manual_credit(self.admin,{'user_id':self.uid,'hours':3,'amount':0,'method':'courtesy','confirmed':True,'request_key':'delete-history-courtesy-12345'})['package_id']
        start=stamp((now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0))
        bid=self.store.reserve(self.uid,{'box':1,'start':start,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        self.assertTrue(self.action('delete')['history_preserved'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE id=?',(pid,)).fetchone()[0],2)
            self.assertEqual(c.execute('SELECT status FROM bookings WHERE id=?',(bid,)).fetchone()[0],'confirmed')
        with self.assertRaises(Problem):self.store.login(self.credentials)
        with self.assertRaises(Problem):self.store.register(self.credentials)
        self.assertTrue(next(u for u in self.store.admin_state()['users'] if u['id']==self.uid)['deleted_at'])
    def test_block_persists_after_delete_and_prevents_google_signup(self):
        self.action('block');self.action('delete')
        with self.assertRaises(Problem):self.store.register(self.credentials)
        with self.assertRaises(GoogleError):authenticate(self.store,{'sub':'blocked-google','email':self.credentials['email'],'name':'Blocked','email_verified':True})
        self.action('unblock',email=self.credentials['email']);uid2=self.store.register(self.credentials);self.assertNotEqual(uid2,self.uid)
    def test_linked_google_and_recovery_cannot_bypass_block(self):
        _,_=authenticate(self.store,{'sub':'linked-google','email':self.credentials['email'],'name':'Linked','email_verified':True},link_session=self.token)
        reset,_=self.store.issue_password_reset(self.credentials['email'])
        self.action('block')
        with self.assertRaises(GoogleError):authenticate(self.store,{'sub':'linked-google','email':self.credentials['email'],'name':'Linked','email_verified':True})
        with self.assertRaises(Problem):self.store.reset_password({'token':reset,'password':'ChangedPassword2026!','password_confirmation':'ChangedPassword2026!'})
        with self.assertRaises(Problem):self.store.issue_email_token(self.uid)
    def test_blocked_account_cannot_use_inflight_requests_to_buy_book_or_upload(self):
        token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
        self.store.admin_action(self.admin,{'action':'personal_approval','id':self.uid,'decision':'approved','confirmed':True})
        self.store.manual_credit(self.admin,{'user_id':self.uid,'hours':3,'amount':0,'method':'courtesy','confirmed':True,'request_key':'blocked-inflight-balance-12345'})
        self.action('block')
        with self.assertRaises(Problem):self.store.buy(self.uid,{'catalog_id':'1','rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        start=stamp((now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0))
        with self.assertRaises(Problem):self.store.reserve(self.uid,{'box':1,'start':start,'rules_acceptance':{'accepted':True,'version':RULES_VERSION}})
        with self.assertRaises(Problem):self.store.upload(self.uid,{'kind':'identity','filename':'blocked.pdf','content':base64.b64encode(b'fixture').decode()})
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM bookings').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT SUM(remaining) FROM packages WHERE user_id=?',(self.uid,)).fetchone()[0],3)
    def test_actions_require_owner_explicit_confirmation_and_protect_administrator(self):
        for action in ('block','delete'):
            for actor,body in [(self.uid,{'user_id':self.uid,'confirmed':True}),(self.admin,{'user_id':self.admin,'confirmed':True}),(self.admin,{'user_id':self.uid,'confirmed':False})]:
                with self.subTest(action=action,actor=actor,body=body),self.assertRaises(Problem):self.store.manage_user_access(actor,{'action':action}|body)
        self.store.session(self.admin_token);self.store.session(self.token)
