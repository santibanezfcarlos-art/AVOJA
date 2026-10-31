import base64
import io
import tempfile
import unittest
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from openpyxl import Workbook
from app import Store, Handler, ADMIN_EMAIL
from whatsapp_campaigns import action, report, read_contacts

def source():
    w=Workbook();w.active.append(['Nombre','Apellido','Correo electrónico','Número de teléfono'])
    for row in [('ANA','Pérez','ana@example.cl','+56 9 1234 5678'),('Ana','Pérez','other@example.cl','912345678'),('Luis','Soto','luis@example.cl','56987654321'),('Error','Teléfono','','123')]:w.active.append(row)
    stream=io.BytesIO();w.save(stream)
    return base64.b64encode(stream.getvalue()).decode()

class Campaigns(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'data.db')
        self.store.configure_access('test-password-long-enough')
        with self.store.connection() as c:self.actor=c.execute("SELECT id FROM users WHERE role='admin'").fetchone()['id']
        self.body={'content':source(),'confirmed':True}
    def test_preview_dedupe_import_preserves_optout_and_progress(self):
        preview=action(self.store,self.actor,self.body,'preview')
        self.assertEqual((preview['rows'],preview['unique'],preview['duplicates'],len(preview['issues'])),(4,2,1,1))
        self.assertEqual(report(self.store)['contacts'],[])
        action(self.store,self.actor,self.body,'import')
        cid=action(self.store,self.actor,{'name':'Prueba','message':'Hola, {nombre}, prueba & más'},'campaign')['id']
        body={'phone':'56912345678','campaign_id':cid}
        url=action(self.store,self.actor,body,'open')['url']
        self.assertEqual(parse_qs(urlparse(url).query)['text'],['Hola, Ana, prueba & más'])
        self.assertEqual(report(self.store)['progress'][0]['status'],'opened')
        action(self.store,self.actor,body|{'status':'sent_manual'},'status')
        with self.assertRaises(ValueError):action(self.store,self.actor,body,'open')
        action(self.store,self.actor,body,'optout')
        action(self.store,self.actor,self.body,'import')
        result=report(self.store)
        self.assertEqual(len(result['contacts']),2)
        self.assertEqual(result['contacts'][0]['opted_out'],1)
        self.assertEqual(result['progress'][0]['status'],'sent_manual')
        with self.assertRaises(ValueError):action(self.store,self.actor,body,'open')
        with self.store.connection() as c:
            for table in ['bookings','packages','notification_outbox']:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
    def test_exact_email_match_without_creating_accounts(self):
        with self.store.connection() as c:
            c.execute("INSERT INTO users(name,email,password,role) VALUES('Ana','ana@example.cl','unused','professional')")
        self.assertEqual(action(self.store,self.actor,self.body,'preview')['registered'],1)
        action(self.store,self.actor,self.body,'import')
        self.assertTrue(report(self.store)['contacts'][0]['registered'])

    def test_direct_web_link_preserves_emoji_and_cleans_rich_text_links(self):
        action(self.store,self.actor,self.body,'import')
        message='Hola, {nombre}, 👋\n🌿 ✨ 👉 [https://valhallaoffice.cl](https://valhallaoffice.cl)'
        cid=action(self.store,self.actor,{'name':'Emojis','message':message},'campaign')['id']
        result=action(self.store,self.actor,{'phone':'56912345678','campaign_id':cid},'open')
        parsed=urlparse(result['web_url'])
        self.assertEqual(parsed.netloc,'web.whatsapp.com')
        self.assertEqual(parse_qs(parsed.query)['text'],['Hola, Ana, 👋\n🌿 ✨ 👉 https://valhallaoffice.cl'])
        self.assertNotIn('+',parsed.query)
        self.assertIn('%F0%9F%91%8B',parsed.query)
        self.assertEqual(parse_qs(urlparse(result['url']).query)['text'],parse_qs(parsed.query)['text'])

    def test_http_owner_only_csrf_and_import(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            owner=self.store.login({'email':ADMIN_EMAIL,'password':'test-password-long-enough'})
            credentials={'name':'Persona prueba','email':'http@example.cl','password':'SyntheticPassword123','specialty':'Psicología'}
            self.store.register(credentials);professional=self.store.login(credentials)
            base=f'http://127.0.0.1:{server.server_port}/api/admin/whatsapp'
            for endpoint in ('preview','import','campaign','open','status','optout'):
                for token,csrf,expected in [('', '',401),(professional,self.store.session(professional)['csrf'],403),(owner,'invalid',403)]:
                    request=urllib.request.Request(base+'/'+endpoint,data=json.dumps(self.body).encode(),headers={'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code,expected)
            for endpoint in ('preview','import'):
                request=urllib.request.Request(base+'/'+endpoint,data=json.dumps(self.body).encode(),headers={'Content-Type':'application/json','Cookie':'session='+owner,'X-CSRF-Token':self.store.session(owner)['csrf']})
                with urllib.request.urlopen(request) as response:self.assertEqual(json.load(response)['unique'],2)
            with urllib.request.urlopen(urllib.request.Request(base,headers={'Cookie':'session='+owner})) as response:self.assertEqual(len(json.load(response)['contacts']),2)
            for token in ('',professional):
                with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(base,headers={'Cookie':'session='+token}))
                self.assertIn(error.exception.code,(401,403))
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
