import concurrent.futures
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from app import Store,Problem,ADMIN_EMAIL,now,stamp,CHILE
from payments import PaymentService,PaymentError
from rules import RULES_VERSION
from test_migration_payments import FakeGateway

class BalanceAndArchive(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.store=Store(Path(self.temp.name)/'balance.sqlite3');self.store.configure_access('BalanceAdmin2026!')
  self.token=self.store.login({'email':ADMIN_EMAIL,'password':'BalanceAdmin2026!'})
  self.admin=self.store.session(self.token)['id']
  self.credentials={'name':'Profesional Migración','email':'balance@example.cl','password':'BalancePro2026!','specialty':'Psicología'}
  self.uid=self.store.register(self.credentials);token,_=self.store.issue_email_token(self.uid);self.store.confirm_email(token)
  self.protoken=self.store.login(self.credentials)
  self.store.import_legacy(self.admin,{'name':self.credentials['name'],'email':self.credentials['email'],'specialty':'Psicología','sessions':5,'reference':'migration-balance-001','note':'Saldo anterior verificado','identity_confirmed':True})
  self.pid=self.state()['packages'][0]['id']
  self.body={'user_id':self.uid,'package_id':self.pid,'delta':-3,'expected_remaining':5,'reason':'Tres atenciones realizadas fuera de la plataforma','request_key':'balance-request-key-123456'}
  self.rules={'accepted':True,'version':RULES_VERSION}
 def state(self):return self.store.state(self.store.session(self.protoken))
 def adjust(self,**changes):return self.store.adjust_sessions(self.admin,self.body|changes)
 def pending(self):return self.store.buy(self.uid,{'catalog_id':'5','rules_acceptance':self.rules})
 def archive(self,pid):return self.store.archive_package(self.admin,{'package_id':pid,'reason':'Compra abandonada sin pago acreditado'})
 def test_five_to_two_then_increase_preserves_price_hours_and_money_history(self):
  self.assertEqual(self.adjust()['remaining'],2);self.adjust(delta=2,expected_remaining=2,request_key='balance-second-key-12345')
  plan=self.state()['packages'][0];self.assertEqual((plan['hours'],plan['remaining'],plan['price'],plan['balance_adjustment']),(5,4,0,-1))
  records=self.store.admin_state()['session_adjustments'];self.assertEqual(records[-1]['before_balance'],5);self.assertEqual(records[-1]['after_balance'],2);self.assertEqual(records[-1]['operator_name'],'Administración Valhalla')
  self.assertNotIn('session_adjustments',self.state());self.assertNotIn(self.body['reason'],json.dumps(self.state()))
  self.assertEqual(self.store.admin_state()['manual_credits'],[])
 def test_retry_and_parallel_old_balance_cannot_double_subtract(self):
  first=self.adjust();self.assertTrue(self.adjust()['repeated']);self.assertEqual(self.state()['packages'][0]['remaining'],2)
  with self.assertRaises(Problem):self.adjust(delta=-1)
  def attempt(key):
   try:return self.adjust(delta=-1,expected_remaining=2,request_key=key)['remaining']
   except Problem:return None
  with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(attempt,['balance-parallel-11111','balance-parallel-22222']))
  self.assertEqual(sum(item is not None for item in result),1);self.assertEqual(self.state()['packages'][0]['remaining'],1)
 def test_invalid_negative_stale_and_wrong_user_rejected_without_changes(self):
  for changes in ({'delta':-6},{'delta':0},{'delta':True},{'expected_remaining':4},{'user_id':self.admin},{'reason':'x'},{'delta':201}):
   with self.subTest(changes=changes),self.assertRaises(Problem):self.adjust(**changes)
  self.assertEqual(self.state()['packages'][0]['remaining'],5)
  with self.assertRaises(Problem):self.store.adjust_sessions(self.uid,self.body)
  with self.assertRaises(Problem):self.store.archive_package(self.uid,{'package_id':self.pid,'reason':'Not authorized'})
 def test_reserved_sessions_unchanged_and_stale_adjustment_refused(self):
  start=stamp((now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0))
  bid=self.store.reserve(self.uid,{'box':1,'start':start})
  with self.assertRaises(Problem):self.adjust()
  self.adjust(delta=-3,expected_remaining=4);state=self.state();self.assertEqual(state['packages'][0]['remaining'],1);self.assertEqual(state['bookings'][0]['id'],bid)
  self.store.admin_action(self.admin,{'action':'cancel','id':bid});self.assertEqual(self.state()['packages'][0]['remaining'],2)
 def test_pending_simulation_hidden_not_deleted_and_cannot_be_confirmed_or_adjusted(self):
  pid=self.pending();self.archive(pid);self.assertTrue(self.archive(pid)['repeated'])
  self.assertNotIn(pid,[plan['id'] for plan in self.state()['packages']]);self.assertEqual(len(self.store.admin_state()['package_archive_events']),1)
  archived=next(plan for plan in self.store.admin_state()['packages'] if plan['id']==pid);self.assertTrue(archived['archived_at']);self.assertEqual(archived['status'],'pending')
  with self.assertRaises(Problem):self.store.admin_action(self.admin,{'action':'payment','id':pid,'decision':'paid'})
  with self.assertRaises(Problem):self.adjust(package_id=pid)
  with self.assertRaises(Problem):self.archive(self.pid)
 def test_webpay_archive_allows_new_checkout_and_late_verified_payment_restores_credit_once(self):
  gateway=FakeGateway();service=PaymentService(self.store,gateway)
  checkout=service.create(self.uid,'5','archive-webpay-first-1234',rules_acceptance=self.rules)
  with self.store.connection() as c:oldpid=c.execute('SELECT package_id FROM payment_attempts WHERE id=?',(checkout['payment_id'],)).fetchone()[0]
  self.archive(oldpid);self.assertEqual(self.state()['payments'],[])
  new=service.create(self.uid,'10','archive-webpay-second-123',rules_acceptance=self.rules);self.assertNotEqual(new['payment_id'],checkout['payment_id'])
  service.reconcile(checkout['payment_id'],self.uid);self.assertNotIn(oldpid,[plan['id'] for plan in self.state()['packages']])
  gateway.transactions[checkout['token']]['status']='AUTHORIZED';service.reconcile(checkout['payment_id'],self.uid);service.reconcile(checkout['payment_id'],self.uid)
  plan=next(plan for plan in self.state()['packages'] if plan['id']==oldpid);self.assertEqual(plan['remaining'],5);self.assertFalse(plan['archived_at'])
  self.assertEqual(len(self.store.admin_state()['package_archive_events']),1)
 def test_rejected_webpay_can_be_archived_even_after_commit_was_requested(self):
  gateway=FakeGateway();service=PaymentService(self.store,gateway);checkout=service.create(self.uid,'5','archive-declined-bank-1234',rules_acceptance=self.rules)
  gateway.transactions[checkout['token']]['status']='FAILED';service.reconcile(checkout['payment_id'],self.uid,commit=True)
  with self.store.connection() as c:pid=c.execute('SELECT package_id FROM payment_attempts WHERE id=?',(checkout['payment_id'],)).fetchone()[0]
  self.archive(pid);self.assertNotIn(pid,[plan['id'] for plan in self.state()['packages']])
 def test_webpay_uncertain_or_inflight_requires_verification_before_archive(self):
  gateway=FakeGateway();service=PaymentService(self.store,gateway);checkout=service.create(self.uid,'5','archive-webpay-safety-123',rules_acceptance=self.rules)
  with self.store.connection(True) as c:
   pid=c.execute('SELECT package_id FROM payment_attempts WHERE id=?',(checkout['payment_id'],)).fetchone()[0]
   c.execute("UPDATE payment_attempts SET state='uncertain' WHERE id=?",(checkout['payment_id'],))
  with self.assertRaises(Problem):self.archive(pid)
  with self.store.connection(True) as c:c.execute("UPDATE payment_attempts SET state='ready',processing_until=9999999999 WHERE id=?",(checkout['payment_id'],))
  with self.assertRaises(Problem):self.archive(pid)
 def test_existing_database_upgrade_preserves_plans_and_rebuilds_open_payment_index(self):
  pending=self.pending()
  with self.store.connection(True) as c:
   c.execute('DROP INDEX one_open_webpay_per_user')
   c.execute('ALTER TABLE payment_attempts DROP COLUMN archived')
   c.execute("CREATE UNIQUE INDEX one_open_webpay_per_user ON payment_attempts(user_id) WHERE state IN ('creating','ready','uncertain','review')")
   for column in ('archived_at','archive_reason','archived_by'):c.execute('ALTER TABLE packages DROP COLUMN '+column)
  self.store=Store(self.store.path)
  self.assertEqual(next(plan for plan in self.state()['packages'] if plan['id']==self.pid)['remaining'],5)
  self.archive(pending);self.assertNotIn(pending,[plan['id'] for plan in self.state()['packages']])
  with self.store.connection() as c:self.assertIn('archived=0',c.execute("SELECT sql FROM sqlite_master WHERE name='one_open_webpay_per_user'").fetchone()[0])
 def test_http_routes_require_admin_and_csrf(self):
  import threading,http.client
  from http.server import ThreadingHTTPServer
  from app import Handler
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for route,body in [('/api/admin/session-adjustments',self.body),('/api/admin/packages/archive',{'package_id':self.pending(),'reason':'Abandoned test plan'})]:
    for token,csrf,status in [('', '',401),(self.protoken,self.store.session(self.protoken)['csrf'],403),(self.token,'',403),(self.token,self.store.session(self.token)['csrf'],200)]:
     conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('POST',route,json.dumps(body),{'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf});response=conn.getresponse();response.read();self.assertEqual(response.status,status);conn.close()
  finally:server.shutdown();server.server_close();thread.join()
