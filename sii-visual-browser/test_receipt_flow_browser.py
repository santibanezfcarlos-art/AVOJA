"""Real Chromium against local fixtures only: no SII login, issue or mail."""
import base64
import json
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from app import create_sii_context
from issue_canary import issue_once, official_snapshot
from test_issue_canary import ORDER, IDS

PDF = b"%PDF-1.4\n" + b"fixture-original-pdf " * 10 + b"\n%%EOF\n"


class ReceiptBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cert = Path(cls.folder.name) / "cert.pem"
        key = Path(cls.folder.name) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-nodes", "-keyout", str(key), "-out", str(cert),
                        "-days", "1", "-subj", "/CN=localhost"],
                       check=True, capture_output=True)
        cls.pdf_requests = 0

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.pdf_requests += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(PDF)))
                self.end_headers()
                self.wfile.write(PDF)

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        cls.server.socket = tls.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.folder.cleanup()

    def setUp(self):
        # Only the LOCAL fixture certificate is ignored; production does not
        # disable TLS validation. The real context permission policy is used.
        browser = self.browser

        class FixtureBrowser:
            def new_context(self, **kwargs):
                return browser.new_context(**kwargs, ignore_https_errors=True)

        self.context = create_sii_context(FixtureBrowser())
        self.page = self.context.new_page()
        self.mail_requests = []
        self.unexpected = []
        self.download_count = 0
        pdf_url = "https://localhost:" + str(self.server.server_port) + "/boleta.pdf"
        fields = "".join('<input id="' + IDS[k] + '" value="' + ORDER[k] + '">'
                         for k in IDS)
        # The two SII actions are on separate forms, with another Emitir and
        # a WhatsApp link nearby to catch overly broad selectors.
        html = '''<meta charset="utf-8"><form id="tax">Boleta exenta $30.000FIELDS
          <button type="button" onclick="issue()">Emitir</button></form>
        <section id="receipt" hidden>
          <a href="PDFURL">get_app Descargar</a>
          <form id="mailForm"><label for="mail">E-mail</label>
            <input id="mail" value="example@example.invalid">
            <button type="button" onclick="sendMail()">send</button></form>
          <form><label for="phone">Número de Contacto</label><input id="phone">
            <a href="https://invalid.invalid/whatsapp" target="_blank">send</a></form>
        </section><div class="v-snack--active" hidden><div class="success">Enviado</div></div>
        <script>
        window.issueClicks=0;window.mailClicks=0;
        function issue(){window.issueClicks++;document.querySelector('#tax').hidden=true;
          document.querySelector('#receipt').hidden=false;
          const receipt={emitido:true,compartirData:{folio:'615',dte:41,monto:30000},
            b64Pdf:PDFDATA,urlPdf:'PDFURL'};
          const wrapper={$parent:receipt,$children:[]};
          receipt.$children=[wrapper];document.body.__vue__=wrapper;}
        async function sendMail(){window.mailClicks++;await fetch('/fixture-email',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({to:document.querySelector('#mail').value,folio:'615',dte:41,monto:30000})});
          document.querySelector('.v-snack--active').hidden=false;}
        </script>'''.replace("FIELDS", fields).replace("PDFURL", pdf_url).replace(
            "PDFDATA", json.dumps(base64.b64encode(PDF).decode()))

        def fixture(route):
            if route.request.url == "https://eboleta.sii.cl/emitir/":
                route.fulfill(status=200, content_type="text/html", body=html)
            elif route.request.url == "https://eboleta.sii.cl/fixture-email":
                self.mail_requests.append(route.request.post_data_json)
                route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            else:
                self.unexpected.append(route.request.url)
                route.abort()

        self.context.route("**/*", fixture)
        self.page.goto("https://eboleta.sii.cl/emitir/")

    def tearDown(self):
        self.context.close()

    def test_snapshot_rejects_unconfirmed_wrong_amount_and_ambiguous_receipts(self):
        self.page.evaluate("issue()")
        self.assertEqual(official_snapshot(self.page, 30000)["folio"], "615")
        self.assertIsNone(official_snapshot(self.page, 30001))
        self.page.evaluate("document.body.__vue__.$parent.emitido=false")
        self.assertIsNone(official_snapshot(self.page, 30000))
        self.page.evaluate("""() => {
          const vm=document.body.__vue__.$parent;vm.emitido=true;
          vm.$children.push({emitido:true,compartirData:{folio:'616',dte:41,monto:30000}});
        }""")
        self.assertIsNone(official_snapshot(self.page, 30000))

    def test_location_is_denied_immediately_without_coordinates_or_prompt(self):
        status = self.page.evaluate('''async () => ({
          state:(await navigator.permissions.query({name:'geolocation'})).state,
          code:await new Promise(r=>navigator.geolocation.getCurrentPosition(
            ()=>r(0),e=>r(e.code),{timeout:1000}))})''')
        self.assertEqual(status, {"state": "denied", "code": 1})
        self.assertEqual(self.unexpected, [])

    def test_one_issue_download_original_store_then_company_mail_in_same_tab(self):
        events = []

        def claim(ticket):
            events.append("issue_claim")

        def store(ticket, snapshot):
            if snapshot["pdf"] is None:
                events.append("folio_recorded")
                return {"pdf_stored":False}
            self.assertEqual(base64.b64decode(snapshot["pdf"]), PDF)
            self.assertEqual(self.page.evaluate("window.mailClicks"), 0)
            events.append("stored")
            return {"pdf_stored": True, "sii_recipient": "contacto@pscsantibanez.cl"}

        def delivery(ticket, folio, action, outcome=None, recipient=None):
            self.assertIn("stored", events)
            events.append("mail_" + action)
            if action == "claim":
                self.assertEqual(recipient, "contacto@pscsantibanez.cl")
                return {"claimed": True, "recipient": recipient}
            return {"state": outcome}

        downloads = []
        self.page.on("download", lambda d: downloads.append(d))
        with (patch("issue_canary.claim", side_effect=claim),
              patch("issue_canary.report_complete", side_effect=store),
              patch("sii_email_after_issue._api", side_effect=delivery)):
            outcome = issue_once(self.page, ORDER, IDS, "offline-ticket")
        self.assertEqual(outcome, "canary_issued_pdf_saved_sii_accepted")
        self.assertEqual(events, ["issue_claim", "folio_recorded", "stored", "mail_claim", "mail_complete"])
        self.assertEqual(len(downloads), 1)
        self.assertEqual(len(self.context.pages), 1)
        self.assertEqual(self.page.url, "https://eboleta.sii.cl/emitir/")
        self.assertEqual(self.page.evaluate("[window.issueClicks,window.mailClicks]"), [1, 1])
        self.assertEqual(len(self.mail_requests), 1)
        self.assertEqual(self.mail_requests[0]["to"], "contacto@pscsantibanez.cl")
        self.assertEqual(self.unexpected, [])


if __name__ == "__main__":
    unittest.main()
