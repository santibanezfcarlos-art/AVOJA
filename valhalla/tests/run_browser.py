"""Disposable app and local fake mailbox. No production email or test bypass in app.py."""
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from app import Handler, Store
    class FakeDelivery:
        configured=True
        def __init__(self):self.tokens={};self.notifications=[];self.notification_keys=set()
        def send_notification(self,email,kind,payload,key):
            from notifications import notification_message
            if key in self.notification_keys:return
            self.notification_keys.add(key)
            subject,text=notification_message(kind,payload,"https://valhallaoffice.cl")
            self.notifications.append({"recipient":email,"kind":kind,"subject":subject,"text":text})
        def send_test(self,email):self.tokens["test:"+email]="accepted"
        def send_verification(self,email,token):self.tokens[email]=token
        def send_password_reset(self,email,token):self.tokens["reset:"+email]=token
    delivery=FakeDelivery()
    class Mailbox(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            email=parse_qs(urlsplit(self.path).query).get('email',[''])[0]
            token=delivery.tokens.get(email)
            notifications=[item for item in delivery.notifications if item['recipient']==email]
            raw=json.dumps({'token':token,'notifications':notifications}).encode()
            self.send_response(200 if token or notifications else 404)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    with tempfile.TemporaryDirectory() as directory:
        private=secrets.token_urlsafe(32)
        store=Store(Path(directory)/'test.sqlite3');store.configure_access(private)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=store;server.email_delivery=delivery
        script=sys.argv[1] if len(sys.argv)>1 else 'browser.cjs'
        if script not in ('browser.cjs','migration_payments_browser.cjs','google_browser.cjs','transbank_browser.cjs','batch_browser.cjs','manual_credits_browser.cjs','confirmation_browser.cjs','live_updates_browser.cjs','balance_archive_browser.cjs','discounts_browser.cjs','personal_approval_browser.cjs','booking_rules_browser.cjs','production_browser.cjs','owner_privileges_browser.cjs','user_access_browser.cjs','skedu_contacts_browser.cjs','microsoft_browser.cjs','management_browser.cjs'):
            raise ValueError('Unknown browser suite')
        if script=='transbank_browser.cjs':
            from payments import PaymentService, WebpayGateway
            server.webpay=PaymentService(store,WebpayGateway('disabled'))
        if script=='production_browser.cjs':
            sys.path.insert(0,str(root/'tests'))
            from test_production_payments import FakeProductionGateway
            from payments import PaymentService
            server.webpay=PaymentService(store,FakeProductionGateway())
        if script in ('migration_payments_browser.cjs','balance_archive_browser.cjs'):
            sys.path.insert(0,str(root/'tests'))
            from test_migration_payments import FakeGateway
            from payments import PaymentService
            server.webpay=PaymentService(store,FakeGateway())
        if script=='management_browser.cjs':
            from app import DEFAULT_MANAGEMENT_EMAIL,ADMIN_EMAIL,CHILE,stamp,now
            from datetime import timedelta
            manager_password='SyntheticManagerBrowser2026!'
            manager=store.register({'name':'Angela Contreras','email':DEFAULT_MANAGEMENT_EMAIL,'specialty':'Psicología','password':manager_password})
            token,_=store.issue_email_token(manager);store.confirm_email(token)
            other=store.register({'name':'Profesional Oficina','email':'office-test@example.cl','specialty':'Psicología','password':'SyntheticOfficeBrowser2026!'})
            token,_=store.issue_email_token(other);store.confirm_email(token)
            with store.connection() as c:owner=c.execute('SELECT id FROM users WHERE email=?',(ADMIN_EMAIL,)).fetchone()[0]
            store.import_legacy(owner,{'name':'Profesional Oficina','email':'office-test@example.cl','specialty':'Psicología','reference':'management-office-test','sessions':5,'identity_confirmed':True})
            booking_day=(now().astimezone(CHILE)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0)
            store.reserve(other,{'box':1,'start':stamp(booking_day)})
        mailbox=ThreadingHTTPServer(('127.0.0.1',0),Mailbox)
        from notifications import NotificationDispatcher
        stop_notifications=threading.Event();notifier=NotificationDispatcher(store,delivery)
        def notify():
            while not stop_notifications.is_set():
                notifier.dispatch_pending()
                stop_notifications.wait(.05)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (server,mailbox)]
        threads.append(threading.Thread(target=notify,daemon=True))
        for thread in threads:thread.start()
        env=os.environ.copy();env['VALHALLA_TEST_ADMIN_PASSWORD']=private
        env['VALHALLA_TEST_URL']=f'http://127.0.0.1:{server.server_port}'
        env['VALHALLA_TEST_MAILBOX_URL']=f'http://127.0.0.1:{mailbox.server_port}'
        if script=='management_browser.cjs':
            env['VALHALLA_TEST_MANAGER_PASSWORD']=manager_password
            env['VALHALLA_TEST_MANAGER_ID']=str(manager)
            env['VALHALLA_TEST_OTHER_ID']=str(other)
            env['VALHALLA_TEST_BOOKING_DAY']=booking_day.date().isoformat()
        if script=='skedu_contacts_browser.cjs':
            sys.path.insert(0,str(root/'tests'))
            from test_skedu_contacts import workbook_bytes
            fixture=Path(directory)/'skedu-test.xlsx';fixture.write_bytes(workbook_bytes())
            env['VALHALLA_TEST_XLSX']=str(fixture)
        try:return subprocess.run(['node','tests/'+script],cwd=root,env=env).returncode
        finally:
            stop_notifications.set()
            for s in (server,mailbox):s.shutdown();s.server_close()
            for thread in threads:thread.join()


if __name__=='__main__':raise SystemExit(main())
