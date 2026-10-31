"""Synthetic data only. Tests never connect to WhatsApp or production."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from app import Store,Handler,ADMIN_EMAIL
from whatsapp_campaigns import action
from whatsapp_runner import issue,api

spec=importlib.util.spec_from_file_location('desktop_core',Path(__file__).resolve().parents[1]/'windows_whatsapp'/'core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)

class Backend(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.s=Store(Path(self.tmp.name)/'db');self.s.configure_access('TestPasswordLong123')
        with self.s.connection(True) as c:
            self.actor=c.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            for p in ('56911111111','56922222222','56933333333','56944444444'):
                c.execute('INSERT INTO whatsapp_contacts VALUES(?,?,?,?,?,?)',(p,'Nombre Prueba','Ángela','[]',0,'now'))
        self.cid=action(self.s,self.actor,{'name':'Prueba','message':'¡Hola, {nombre}! 👋\nhttps://valhallaoffice.cl'},'campaign')['id']
        action(self.s,self.actor,{'phone':'56922222222'},'optout')
        for p,status in [('56933333333','skipped'),('56944444444','sent_manual')]:
            action(self.s,self.actor,{'campaign_id':self.cid,'phone':p,'status':status},'status')
        self.grant=issue(self.s,self.actor,{'campaign_id':self.cid,'phones':['56911111111','56922222222','56933333333','56944444444']})
        self.token=self.grant['token']
    def call(self,op,**body):return api(self.s,self.token,op,body)
    def claim(self):return self.call('claim',phone='56911111111')['attempt']
    def test_queue_personalization_and_exclusions(self):
        q=self.call('queue');self.assertEqual(len(q['recipients']),1)
        self.assertEqual(q['counts'],{'total':4,'registered':0,'not_registered':4,'opted_out':1})
        self.assertEqual(q['recipients'][0]['message'],'¡Hola, Ángela! 👋\nhttps://valhallaoffice.cl')
        for p in ['56922222222','56933333333','56944444444','56955555555']:
            with self.assertRaises(ValueError):self.call('claim',phone=p)
    def test_duplicate_claim_even_after_manual_reset(self):
        self.claim()
        action(self.s,self.actor,{'campaign_id':self.cid,'phone':'56911111111','status':'pending'},'status')
        with self.assertRaises(ValueError):self.claim()
        self.assertEqual(self.call('queue')['recipients'],[])
        with self.assertRaises(ValueError):action(self.s,self.actor,{'campaign_id':self.cid,'phone':'56911111111'},'open')
    def test_optout_after_prepare_blocks_send(self):
        aid=self.claim();self.call('event',attempt=aid,status='runner_prepared')
        action(self.s,self.actor,{'phone':'56911111111'},'optout')
        with self.assertRaises(ValueError):self.call('check',attempt=aid)
        with self.assertRaises(ValueError):self.call('event',attempt=aid,status='runner_actioned')
        self.call('event',attempt=aid,status='runner_failed')
    def test_transitions_and_idempotent_ack(self):
        aid=self.claim()
        with self.assertRaises(ValueError):self.call('event',attempt=aid,status='runner_confirmed_ui')
        for st in ('runner_prepared','runner_actioned','runner_uncertain'):
            self.call('event',attempt=aid,status=st);self.call('event',attempt=aid,status=st)
        with self.assertRaises(ValueError):self.call('event',attempt=aid,status='runner_prepared')
    def test_expiry_wrong_token_and_campaign_scope(self):
        with self.assertRaises(PermissionError):api(self.s,'incorrect','queue',{})
        with self.s.connection(True) as c:c.execute('UPDATE whatsapp_runner_grants SET expires=0')
        with self.assertRaises(PermissionError):self.call('queue')
    def test_runner_to_backend_end_to_end(self):
        backend=self
        class DirectAPI:
            def call(self,op,**body):return backend.call(op,**body)
            def flush(self,journal):
                for id,attempt,status,reason in journal.pending():
                    self.call('event',attempt=attempt,status=status,reason=reason);journal.ack(id)
        journal=core.Journal(Path(self.tmp.name)/'journal')
        ui=FakeUI();item=self.call('queue')['recipients'][0]
        runner=core.Runner(journal,ui,lambda:None,lambda t:None,DirectAPI())
        self.assertTrue(runner.run_one(self.cid,item));self.assertEqual(ui.sent,1)
        with self.s.connection() as c:
            self.assertEqual(c.execute('SELECT status FROM whatsapp_runner_attempts').fetchone()[0],'runner_confirmed_ui')
        self.assertEqual(self.call('queue')['recipients'],[])
        self.assertFalse(runner.run_one(self.cid,item));self.assertEqual(ui.sent,1)
    def test_concurrent_claim_only_one_wins(self):
        results=[]
        def run():
            try:results.append(self.claim())
            except ValueError:results.append(None)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(bool(r) for r in results),1)
    def test_http_auth_and_csrf(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.s
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            def req(path,body,headers={}):return Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**headers})
            with self.assertRaises(HTTPError) as e:urlopen(req('/api/whatsapp-runner/queue',{}))
            self.assertEqual(e.exception.code,403)
            with urlopen(req('/api/whatsapp-runner/queue',{}, {'Authorization':'Bearer '+self.token})) as r:self.assertEqual(len(json.load(r)['recipients']),1)
            owner=self.s.login({'email':ADMIN_EMAIL,'password':'TestPasswordLong123'})
            with self.assertRaises(HTTPError) as e:urlopen(req('/api/admin/whatsapp/runner-connect',{}, {'Cookie':'session='+owner}))
            self.assertEqual(e.exception.code,403)
            with self.assertRaises(HTTPError) as e:urlopen(req('/api/whatsapp-runner/queue',{}, {'Authorization':'Bearer '+self.token,'Origin':'https://evil.example'}))
            self.assertEqual(e.exception.code,403)
        finally:server.shutdown();server.server_close();thread.join()

class FakeUI:
    def __init__(self,fail=None):self.fail=fail;self.sent=0
    def open_chat(self,p,message):pass
    def verify_recipient(self,p):
        if self.fail=='recipient':raise RuntimeError('wrong recipient')
    def prepare(self,m):pass
    def verify_content(self,m):
        if self.fail=='content':raise RuntimeError('wrong text')
    def send(self):self.sent+=1
    def verify_sent(self,m):
        if self.fail=='confirmation':raise RuntimeError('unknown')

class Desktop(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'journal';self.j=core.Journal(self.path)
        self.item={'phone':'56911111111','message':core.TEST_MESSAGE}
    def state(self):
        with self.j.db() as c:return c.execute('SELECT state FROM attempts').fetchone()[0]
    def run_with(self,ui,gate=lambda:None):return core.Runner(self.j,ui,gate,lambda text:None)
    def test_unverifiable_recipient_or_content_never_sends(self):
        for reason in ('recipient','content'):
            ui=FakeUI(reason)
            with self.assertRaises(RuntimeError):self.run_with(ui).run_one(reason,self.item)
            self.assertEqual(ui.sent,0)
    def test_uncertain_never_retried(self):
        ui=FakeUI('confirmation')
        with self.assertRaises(RuntimeError):self.run_with(ui).run_one('1',self.item)
        self.assertEqual(self.state(),'runner_uncertain')
        self.assertFalse(self.run_with(ui).run_one('1',self.item));self.assertEqual(ui.sent,1)
    def test_restart_blocks_prepared_attempt(self):
        self.j.begin('1',self.item['phone'],self.item['message'],'a')
        self.j.state('1',self.item['phone'],'runner_prepared')
        self.j=core.Journal(self.path);self.assertEqual(self.state(),'runner_uncertain')
        ui=FakeUI();self.assertFalse(self.run_with(ui).run_one('1',self.item));self.assertEqual(ui.sent,0)
    def test_confirmed_ui_is_not_delivery_and_test_unlocks(self):
        ui=FakeUI();self.run_with(ui).run_one('test',self.item,test=True)
        self.assertEqual(self.state(),'runner_confirmed_ui');self.assertTrue(self.j.test_passed())
    def test_stop_does_not_send(self):
        def stop():raise core.Stopped()
        ui=FakeUI()
        with self.assertRaises(core.Stopped):self.run_with(ui,stop).run_one('1',self.item)
        self.assertEqual(ui.sent,0)
    def test_confirmation_sync_failure_preserves_local_proof(self):
        class BrokenAPI:
            def call(self,op,**body):return {'attempt':'a',**self.item} if op=='claim' else {'ok':True}
            def flush(self,j):
                if any(row[2]=='runner_confirmed_ui' for row in j.pending()):raise OSError('network lost')
                for row in j.pending():j.ack(row[0])
        api=BrokenAPI();api.item=self.item;ui=FakeUI()
        runner=core.Runner(self.j,ui,lambda:None,lambda t:None,api)
        with self.assertRaises(OSError):runner.run_one('1',self.item)
        self.assertEqual(ui.sent,1);self.assertEqual(self.state(),'runner_confirmed_ui')
        self.assertEqual([row[2] for row in self.j.pending()],['runner_confirmed_ui'])
    def test_manual_observation_records_only_existing_actioned_self_test(self):
        p=self.item['phone']
        with self.assertRaises(ValueError):self.j.confirm_observed_test(p)
        self.j.begin('prueba-review',p,core.TEST_MESSAGE,'test')
        self.j.state('prueba-review',p,'runner_failed')
        with self.assertRaises(ValueError):self.j.confirm_observed_test(p)
        self.j.state('prueba-review',p,'runner_uncertain')
        self.j.confirm_observed_test(p);self.j.confirm_observed_test(p)
        self.assertTrue(self.j.test_passed())
        self.assertEqual(self.state(),'test_confirmed_by_user')
        with self.j.db() as c:
            self.assertEqual(c.execute('SELECT previous_state FROM test_reviews').fetchall(),[('runner_uncertain',)])
        self.assertEqual(self.j.pending(),[])
    def test_manual_review_does_not_modify_client_campaigns_or_other_message(self):
        p=self.item['phone']
        self.j.begin('123',p,core.TEST_MESSAGE,'remote-attempt')
        self.j.state('123',p,'runner_uncertain')
        with self.assertRaises(ValueError):self.j.confirm_observed_test(p)
        self.j.begin('prueba-other',p,'Otro texto','test')
        self.j.state('prueba-other',p,'runner_uncertain')
        with self.assertRaises(ValueError):self.j.confirm_observed_test(p)
        self.assertFalse(self.j.test_passed())
    def test_write_ahead_failure_never_sends(self):
        class BrokenAPI:
            def call(self,op,**body):return {'attempt':'a',**self.item} if op=='claim' else {'ok':True}
            def flush(self,j):
                if any(row[2]=='runner_actioned' for row in j.pending()):raise OSError('network lost')
                for row in j.pending():j.ack(row[0])
        api=BrokenAPI();api.item=self.item;ui=FakeUI()
        runner=core.Runner(self.j,ui,lambda:None,lambda t:None,api)
        with self.assertRaises(OSError):runner.run_one('1',self.item)
        self.assertEqual(ui.sent,0);self.assertEqual(self.state(),'runner_uncertain')
        self.assertEqual([row[2] for row in self.j.pending()],['runner_actioned','runner_uncertain'])

if __name__=='__main__':unittest.main()
