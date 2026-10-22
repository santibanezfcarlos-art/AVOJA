"""Isolated imports and recipient selection. No email or TTLock network calls."""
import base64
import io
import json
import secrets
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime,time,date
from pathlib import Path
from http.server import ThreadingHTTPServer
from openpyxl import Workbook
from app import Store,ADMIN_EMAIL,Problem,Handler,stamp,CHILE
from skedu_contacts import read_skedu_contacts
from ttlock_recipients import recipients_for_day


def workbook_bytes(rows=None):
    w=Workbook();w.active.append(['First sheet is intentionally ignored'])
    sheet=w.create_sheet('Reservas')
    sheet.append(['Reservas de Skedu']);sheet.append([])
    sheet.append(['Fecha','Box','Inicio','Término','Cliente','Correo','Confirmación','ID reserva'])
    for row in rows or [
        [datetime(2026,10,23),'Box 1',time(9),time(9,55),'Persona Skedu','person@example.cl','No confirmada','skedu-a'],
        [datetime(2026,10,23),'Box 2',time(10),time(10,55),'Persona Skedu','PERSON@example.cl','No confirmada','skedu-b'],
        [datetime(2026,10,24),'Box infantil',time(9),time(10),'Otra Persona','other@example.cl','Confirmada','skedu-c']
    ]:sheet.append(row)
    buf=io.BytesIO();w.save(buf);w.close();return buf.getvalue()


class SkeduContacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'test.sqlite3');self.secret=secrets.token_urlsafe(24)
        self.store.configure_access(self.secret)
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':self.secret}))['id']
        self.body={'content':base64.b64encode(workbook_bytes()).decode(),'include_unconfirmed':True,'confirmed':True}
    def test_second_sheet_parsing_original_times_and_confirmation_filter(self):
        rows,info=read_skedu_contacts(self.body['content'],True)
        self.assertEqual(len(rows),3);self.assertEqual(info['unconfirmed'],2)
        self.assertEqual(rows[2]['end'],'2026-10-24T10:00:00-03:00')
        rows,info=read_skedu_contacts(self.body['content'],False)
        self.assertEqual(sum(row['active'] for row in rows),1);self.assertEqual(info['skipped'],2)
    def test_preview_is_read_only_import_idempotent_and_no_bookings_or_emails(self):
        preview=self.store.import_skedu_contacts(self.admin,self.body,preview=True)
        self.assertEqual(preview['days'][0],{'date':'2026-10-23','bookings':2,'contacts':1})
        self.assertEqual(self.store.daily_contacts('2026-10-23')['emails'],[])
        for _ in range(2):self.store.import_skedu_contacts(self.admin,self.body)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM skedu_daily_contacts').fetchone()[0],3)
            for table in ('bookings','packages','notification_outbox'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        report=self.store.daily_contacts('2026-10-23')
        self.assertEqual((report['total_professionals'],report['total_bookings'],report['total_skedu_bookings']),(1,0,2))
        self.assertEqual(report['emails'],['person@example.cl'])
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,23)),['person@example.cl'])
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,24)),['other@example.cl'])
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,25)),[])
    def test_native_and_external_same_email_deduplicate_and_removal_preserves_native(self):
        credentials={'name':'Persona Nativa','email':'person@example.cl','password':'SyntheticProfessional123','specialty':'Psicología'}
        uid=self.store.register(credentials)
        with self.store.connection(True) as c:
            pid=c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,1,0,0,'paid','2026-10-22')",(uid,)).lastrowid
            c.execute("INSERT INTO bookings(user_id,package_id,box,start,status,created_at) VALUES(?,?,1,?,'confirmed',?)",(uid,pid,stamp(datetime(2026,10,23,9,tzinfo=CHILE)),stamp(datetime(2026,10,22,tzinfo=CHILE))))
        self.store.import_skedu_contacts(self.admin,self.body)
        report=self.store.daily_contacts('2026-10-23');self.assertEqual(report['emails'],['person@example.cl'])
        self.assertEqual(report['contacts'][0]['name'],'Persona Nativa')
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,23)),['person@example.cl'])
        self.store.remove_skedu_contacts(self.admin,{'references':['skedu-a','skedu-b'],'confirmed':True})
        report=self.store.daily_contacts('2026-10-23');self.assertEqual(report['total_skedu_bookings'],0)
        self.assertEqual(report['total_bookings'],1);self.assertEqual(report['emails'],['person@example.cl'])
    def test_cancellation_and_confirmation_filter_remove_future_external_access(self):
        self.store.import_skedu_contacts(self.admin,self.body)
        self.store.import_skedu_contacts(self.admin,self.body|{'include_unconfirmed':False})
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,23)),[])
        self.store.import_skedu_contacts(self.admin,self.body)
        self.store.remove_skedu_contacts(self.admin,{'references':['skedu-a','skedu-b'],'confirmed':True})
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,23)),[])
        rows=[[datetime(2026,10,24),'Box infantil',time(9),time(10),'Otra Persona','other@example.cl','Cancelada','skedu-c']]
        self.store.import_skedu_contacts(self.admin,self.body|{'content':base64.b64encode(workbook_bytes(rows)).decode()})
        self.assertEqual(recipients_for_day(self.store.path,date(2026,10,24)),[])
    def test_automatic_access_delivery_uses_skedu_date_and_sends_once_with_fake_mailer(self):
        import os
        from unittest.mock import patch
        from ttlock_daily import AttemptLedger,encrypt_code
        from ttlock_distribution import distribute
        self.store.import_skedu_contacts(self.admin,self.body)
        class FakeMailer:
            configured=True
            def __init__(self):self.sent=[]
            def send_message(self,email,subject,text,**kwargs):self.sent.append(email)
        environment={'TTLOCK_DISTRIBUTION_ENABLED':'true','TTLOCK_EMAIL_TO':'original-owner@example.cl',
                     'TTLOCK_DISTRIBUTION_TO':'center@example.cl',
                     'TTLOCK_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')}
        with patch.dict(os.environ,environment):
            ledger=AttemptLedger(self.store.path);ledger.initialize();ledger.claim('2026-10-22')
            ledger.save_created('2026-10-22',100,encrypt_code('123456'));ledger.mark('2026-10-22','emailed',code_id=100)
            delivery=FakeMailer()
            self.assertEqual(distribute(self.store.path,'2026-10-22',mailer=delivery),'accepted')
            self.assertEqual(delivery.sent,['center@example.cl','person@example.cl'])
            self.assertEqual(distribute(self.store.path,'2026-10-22',mailer=delivery),'already_attempted')
            self.assertEqual(len(delivery.sent),2)

    def test_invalid_rows_fail_atomically_and_import_requires_owner_confirmation(self):
        with self.assertRaises(Problem):self.store.import_skedu_contacts(self.admin,self.body|{'confirmed':False})
        with self.assertRaises(Problem):self.store.import_skedu_contacts(999,self.body)
        with self.assertRaises(Problem):self.store.import_skedu_contacts(self.admin,self.body|{'content':'bad-data'})
        rows=[ [datetime(2026,10,23),'Box 1',time(9),time(9,55),'Valid Person','person@example.cl','Confirmada','a'],
               [datetime(2026,10,23),'Box 1',time(10),time(10,55),'Invalid Person','invalid','Confirmada','b'] ]
        with self.assertRaises(Problem):self.store.import_skedu_contacts(self.admin,self.body|{'content':base64.b64encode(workbook_bytes(rows)).decode()})
        self.assertEqual(self.store.daily_contacts('2026-10-23')['emails'],[])
    def test_http_import_preview_and_removal_require_admin_and_csrf(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            owner_token=self.store.login({'email':ADMIN_EMAIL,'password':self.secret})
            credentials={'name':'Profesional HTTP','email':'http@example.cl','password':'SyntheticProfessional123','specialty':'Psicología'}
            self.store.register(credentials);professional_token=self.store.login(credentials)
            for endpoint in ('preview','import','remove'):
                url=f'http://127.0.0.1:{server.server_port}/api/admin/daily-contacts/skedu/{endpoint}'
                for token,csrf,expected in [('', '',401),(professional_token,self.store.session(professional_token)['csrf'],403),(owner_token,'invalid',403)]:
                    request=urllib.request.Request(url,data=json.dumps(self.body).encode(),headers={'Content-Type':'application/json','Cookie':'session='+token,'X-CSRF-Token':csrf})
                    with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code,expected)
            request=urllib.request.Request(url.rsplit('/',1)[0]+'/preview',data=json.dumps(self.body).encode(),headers={'Content-Type':'application/json','Cookie':'session='+owner_token,'X-CSRF-Token':self.store.session(owner_token)['csrf']})
            with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['preview'])
        finally:server.shutdown();server.server_close();thread.join()
