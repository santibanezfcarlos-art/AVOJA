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
        def __init__(self):self.tokens={}
        def send_verification(self,email,token):self.tokens[email]=token
        def send_password_reset(self,email,token):self.tokens["reset:"+email]=token
    delivery=FakeDelivery()
    class Mailbox(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            email=parse_qs(urlsplit(self.path).query).get('email',[''])[0]
            token=delivery.tokens.get(email)
            raw=json.dumps({'token':token}).encode()
            self.send_response(200 if token else 404)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
    with tempfile.TemporaryDirectory() as directory:
        private=secrets.token_urlsafe(32)
        store=Store(Path(directory)/'test.sqlite3');store.configure_access(private)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=store;server.email_delivery=delivery
        script=sys.argv[1] if len(sys.argv)>1 else 'browser.cjs'
        if script not in ('browser.cjs','migration_payments_browser.cjs'):
            raise ValueError('Unknown browser suite')
        if script=='migration_payments_browser.cjs':
            sys.path.insert(0,str(root/'tests'))
            from test_migration_payments import FakeGateway
            from payments import PaymentService
            server.webpay=PaymentService(store,FakeGateway())
        mailbox=ThreadingHTTPServer(('127.0.0.1',0),Mailbox)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (server,mailbox)]
        for thread in threads:thread.start()
        env=os.environ.copy();env['VALHALLA_TEST_ADMIN_PASSWORD']=private
        env['VALHALLA_TEST_URL']=f'http://127.0.0.1:{server.server_port}'
        env['VALHALLA_TEST_MAILBOX_URL']=f'http://127.0.0.1:{mailbox.server_port}'
        try:return subprocess.run(['node','tests/'+script],cwd=root,env=env).returncode
        finally:
            for s in (server,mailbox):s.shutdown();s.server_close()
            for thread in threads:thread.join()


if __name__=='__main__':raise SystemExit(main())
