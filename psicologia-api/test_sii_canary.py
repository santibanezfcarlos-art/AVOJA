"""Offline test of the one real SII canary claim; never calls the tax portal."""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import billing
import main

SECRET="offline-sii-one-time-secret-2026-long"
OID="PSCP"+"C"*18


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{
            "RENDER":"","PSICOLOGIA_BILLING_DB":self.tmp.name+"/store.db",
            "PSICOLOGIA_BILLING_ADMIN_PASSWORD":"offline-canary-password",
            "PSICOLOGIA_BILLING_ADMIN_HASH":"",
            "SII_CANARY_ENABLED":"true",
            "SII_VISUAL_BROWSER_URL":"https://psc-sii-visual-test.onrender.com",
            "SII_VISUAL_TICKET_SECRET":SECRET})
        self.env.start()
        self.client=main.app.test_client()
        person={"rut":"12345678-5","name":"Giovanni Ejemplo","email":"example@example.invalid",
                "address":"Dirección ficticia 123","phone":"+56912345678"}
        billing.prepare(OID,"psicoterapia","2026-11-10","10:00",30000,person,"production")
        billing.bind_token(OID,"Z"*40)
        billing.record_result(OID,"Z"*40,30000,True,"VD")
        self.auth=("carlos","offline-canary-password")
        self.bridge={"X-PSC-Visual-Bridge":SECRET}
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def ticket(self,oid=OID,mode="issue_canary",nonce="one-off-claim-example-123456"):
        data={"exp":int(time.time())+600,"nonce":nonce,
              "preview_order":oid,"mode":mode}
        payload=base64.urlsafe_b64encode(json.dumps(data,separators=(",",":")).encode()).rstrip(b"=").decode()
        mac=hmac.new(SECRET.encode(),payload.encode(),hashlib.sha256).digest()
        return payload+"."+base64.urlsafe_b64encode(mac).rstrip(b"=").decode()

    def claim(self,ticket=None,headers=None):
        return self.client.post("/admin/sii-visual/internal-canary-claim",
                                json={"ticket":ticket or self.ticket()},
                                headers=headers or self.bridge)

    def test_requires_signed_ticket_and_server_secret(self):
        self.assertEqual(self.claim(headers={"X-PSC-Visual-Bridge":"wrong"}).status_code,403)
        self.assertEqual(self.claim("forged.ticket").status_code,403)
        self.assertEqual(self.claim(self.ticket(mode="preview")).status_code,403)

    def test_first_claim_is_atomic_and_subsequent_claims_rejected(self):
        first=self.claim()
        self.assertEqual(first.status_code,200,first.text)
        self.assertTrue(first.json["claimed"])
        self.assertEqual(self.claim().status_code,409)
        with billing.db() as con:
            current=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(OID,)).fetchone()
            self.assertEqual(current["status"],"reconciliation")
            n=con.execute("SELECT count(*) FROM sii_first_canary").fetchone()[0]
            self.assertEqual(n,1)
            b=con.execute("SELECT invoice_status,folio FROM billing_orders WHERE order_id=?",(OID,)).fetchone()
            self.assertEqual(b["invoice_status"],"pending")
            self.assertIsNone(b["folio"])

    def test_admin_folio_reconciles_exact_once(self):
        self.assertEqual(self.claim().status_code,200)
        today=datetime.now(ZoneInfo("America/Santiago")).date().isoformat()
        marked=self.client.post("/admin/boletas/"+OID+"/issued",
            json={"folio":"716","issued_date":today},auth=self.auth,
            headers={"X-Billing-Action":"manual-invoice"})
        self.assertEqual(marked.status_code,200,marked.text)
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT state FROM sii_first_canary").fetchone()[0],"issued")
            self.assertEqual(con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(OID,)).fetchone()[0],"issued")

    def test_issue_launch_is_order_specific_and_gated(self):
        before=self.client.post("/admin/sii-visual/session",auth=self.auth,
            data={"order_id":OID,"mode":"issue_canary"})
        self.assertEqual(before.status_code,303,before.text)
        ticket=before.location.split("#ticket=")[1]
        payload=ticket.split(".")[0]
        decoded=json.loads(base64.urlsafe_b64decode(payload+"="*(-len(payload)%4)))
        self.assertEqual(decoded["preview_order"],OID)
        self.assertEqual(decoded["mode"],"issue_canary")
        self.assertEqual(self.claim().status_code,200)
        after=self.client.post("/admin/sii-visual/session",auth=self.auth,
            data={"order_id":OID,"mode":"issue_canary"})
        self.assertEqual(after.status_code,409)

    def test_disabled_canary_stays_unavailable(self):
        with patch.dict(os.environ,{"SII_CANARY_ENABLED":"false"}):
            r=self.client.post("/admin/sii-visual/session",auth=self.auth,
                data={"order_id":OID,"mode":"issue_canary"})
            self.assertEqual(r.status_code,503)
            self.assertEqual(self.claim().status_code,403)


if __name__=="__main__":unittest.main()
