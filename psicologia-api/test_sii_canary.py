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
            "SII_CANARY_ENABLED":"true","SII_POST_ISSUE_ENABLED":"true",
            "SII_VISUAL_BROWSER_URL":"https://psc-sii-visual-test.onrender.com",
            "SII_VISUAL_TICKET_SECRET":SECRET})
        self.env.start()
        self.client=main.app.test_client()
        person={"rut":"12345678-5","name":"Giovanni Salas","email":"example@example.invalid",
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
            n=con.execute("SELECT count(*) FROM sii_issuance_attempts").fetchone()[0]
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
            self.assertEqual(con.execute("SELECT state FROM sii_issuance_attempts").fetchone()[0],"issued")
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

    def test_signed_official_callback_persists_folio_and_pdf_and_sends_psc_once(self):
        pdf=b'%PDF-1.4\\n'+b'x'*120+b'\\n%%EOF\\n'
        self.assertEqual(self.claim().status_code,200)
        data={"ticket":self.ticket(),"snapshot":{"folio":"716","dte":41,
              "amount":30000,"pdf":base64.b64encode(pdf).decode()}}
        with patch("sii_receipt_admin.dispatch_psc",return_value="accepted") as deliver:
            result=self.client.post("/admin/sii-visual/internal-canary-complete",
                                    json=data,headers=self.bridge)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json["state"],"issued")
        self.assertTrue(result.json["pdf_stored"])
        deliver.assert_called_once_with(OID)
        with billing.db() as con:
            row=con.execute("SELECT folio,pdf,psc_state FROM sii_receipts WHERE order_id=?",(OID,)).fetchone()
            self.assertEqual(row["folio"],"716")
            self.assertEqual(row["pdf"],pdf)
            self.assertEqual(con.execute("SELECT state FROM sii_issuance_attempts").fetchone()[0],"issued")

    def test_sii_email_claim_only_after_official_pdf_and_once_per_purchase(self):
        self.assertEqual(self.claim().status_code,200)
        pdf=b'%PDF-1.4\\n'+b'x'*120+b'\\n%%EOF\\n'
        data={"ticket":self.ticket(),"snapshot":{"folio":"716","dte":41,
              "amount":30000,"pdf":base64.b64encode(pdf).decode()}}
        with patch("sii_receipt_admin.dispatch_psc",return_value="accepted"):
            self.assertEqual(self.client.post("/admin/sii-visual/internal-canary-complete",
                json=data,headers=self.bridge).status_code,200)
        endpoint="/admin/sii-visual/internal-sii-delivery"
        def send(action,outcome=None):
            payload={"ticket":self.ticket(),"folio":"716","action":action}
            if outcome:payload["outcome"]=outcome
            return self.client.post(endpoint,json=payload,headers=self.bridge)
        self.assertEqual(send("complete","accepted").status_code,409)
        wrong=self.client.post(endpoint,json={"ticket":self.ticket(),"folio":"716",
            "action":"claim","recipient":"other@example.invalid"},headers=self.bridge)
        self.assertEqual(wrong.status_code,409)
        first=send("claim")
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json["recipient"],"contacto@pscsantibanez.cl")
        with billing.db() as con:
            portal=con.execute("SELECT recipient FROM sii_portal_deliveries WHERE order_id=?",(OID,)).fetchone()
            patient=con.execute("SELECT recipient FROM sii_receipts WHERE order_id=?",(OID,)).fetchone()
            self.assertEqual(portal["recipient"],"contacto@pscsantibanez.cl")
            self.assertEqual(patient["recipient"],"example@example.invalid")
        self.assertEqual(send("claim").status_code,409)
        finish=send("complete","accepted")
        self.assertEqual(finish.status_code,200,finish.text)
        self.assertEqual(finish.json["state"],"accepted")
        self.assertEqual(send("complete","accepted").status_code,409)
        with billing.db() as con:
            r=con.execute("SELECT sii_state,sii_reference FROM sii_receipts WHERE order_id=?",(OID,)).fetchone()
            self.assertEqual(r["sii_state"],"accepted")
            self.assertEqual(r["sii_reference"],"sii-portal-716")

    def test_sii_email_unknown_blocks_duplicate(self):
        self.assertEqual(self.claim().status_code,200)
        pdf=b'%PDF-1.4\\n'+b'x'*120+b'\\n%%EOF\\n'
        with patch("sii_receipt_admin.dispatch_psc",return_value="accepted"):
            self.client.post("/admin/sii-visual/internal-canary-complete",
                json={"ticket":self.ticket(),"snapshot":{"folio":"716","dte":41,
                      "amount":30000,"pdf":base64.b64encode(pdf).decode()}},headers=self.bridge)
        route="/admin/sii-visual/internal-sii-delivery"
        d={"ticket":self.ticket(),"folio":"716","action":"claim"}
        self.assertEqual(self.client.post(route,json=d,headers=self.bridge).status_code,200)
        d["action"]="complete";d["outcome"]="unknown"
        self.assertEqual(self.client.post(route,json=d,headers=self.bridge).json["state"],"unknown")
        d["action"]="claim";d.pop("outcome")
        self.assertEqual(self.client.post(route,json=d,headers=self.bridge).status_code,409)

    def test_invalid_completion_does_not_create_folio(self):
        self.assertEqual(self.claim().status_code,200)
        wrong={"ticket":self.ticket(),"snapshot":{"folio":"716","dte":39,
              "amount":30000,"pdf":None}}
        result=self.client.post("/admin/sii-visual/internal-canary-complete",
                                json=wrong,headers=self.bridge)
        self.assertEqual(result.status_code,409)
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT invoice_status FROM billing_orders WHERE order_id=?",(OID,)).fetchone()[0],"pending")

    def test_pre_queue_purchase_like_giovanni_can_start_first_canary(self):
        with billing.db() as con:
            con.execute("DELETE FROM sii_issuance_queue WHERE order_id=?",(OID,))
        response=self.client.post("/admin/sii-visual/session",auth=self.auth,
                                  data={"order_id":OID,"mode":"issue_canary"})
        self.assertEqual(response.status_code,303,response.text)
        with billing.db() as con:
            current=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(OID,)).fetchone()
        self.assertEqual(current["status"],"awaiting_issuer")

    def test_one_click_issues_through_server_to_server_chromium(self):
        with patch("sii_dispatch.threading.Thread") as thread:
            response=self.client.post("/admin/boletas/"+OID+"/emit",auth=self.auth,
                                      headers={"X-Billing-Action":"manual-invoice"})
        self.assertEqual(response.status_code,202,response.text)
        self.assertTrue(response.json["started"])
        kwargs=thread.call_args.kwargs
        from sii_canary import ticket_data
        self.assertEqual(ticket_data(kwargs["args"][2])["preview_order"],OID)
        self.assertEqual(ticket_data(kwargs["args"][2])["mode"],"issue_canary")
        with patch("sii_canary._server_request",return_value=(202,{})) as transport:
            kwargs["target"](*kwargs["args"])
            self.assertEqual(transport.call_args.kwargs["timeout"],90)
            transport.assert_called_once()
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT invoice_status FROM billing_orders WHERE order_id=?",(OID,)).fetchone()[0],"pending")

    def test_cold_start_returns_immediately_and_polling_resumes_without_second_start(self):
        with (patch("sii_dispatch.threading.Thread") as thread,
              patch("sii_canary._server_request") as transport):
            r=self.client.post("/admin/boletas/"+OID+"/emit",auth=self.auth,
                               headers={"X-Billing-Action":"manual-invoice"})
            self.assertEqual(r.status_code,202)
            transport.assert_not_called()
            status=self.client.get("/admin/boletas/"+OID+"/issue-status",auth=self.auth)
            self.assertEqual(status.json["stage"],"render_waking")
            transport.assert_not_called()
            repeat=self.client.post("/admin/boletas/"+OID+"/emit",auth=self.auth,
                                    headers={"X-Billing-Action":"manual-invoice"})
            self.assertEqual(repeat.status_code,409)
            self.assertEqual(thread.call_count,1)

    def test_only_failed_preclaim_is_rearmed_and_audited(self):
        import sii_dispatch
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OID,))
        self.assertTrue(sii_dispatch.reconcile_preclaim(OID,{"state":"not_running"}))
        with billing.db() as con:
            self.assertEqual(con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(OID,)).fetchone()[0],"awaiting_issuer")
            self.assertIsNone(con.execute("SELECT folio FROM billing_orders WHERE order_id=?",(OID,)).fetchone()[0])
            self.assertTrue(con.execute("SELECT 1 FROM billing_audit WHERE action='sii_preclaim_start_failure_rearmed'").fetchone())

    def test_actual_sii_claim_is_never_reset_by_start_recovery(self):
        import sii_dispatch
        self.assertEqual(self.claim().status_code,200)
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OID,))
        self.assertFalse(sii_dispatch.reconcile_preclaim(OID,{"state":"not_running"}))

    def test_active_browser_and_unapproved_payment_are_never_rearmed(self):
        import sii_dispatch
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OID,))
        self.assertFalse(sii_dispatch.reconcile_preclaim(OID,{"state":"open","stage":"filling_receiver"}))
        with billing.db() as con:
            con.execute("UPDATE billing_orders SET payment_status='initiated' WHERE order_id=?",(OID,))
        self.assertFalse(sii_dispatch.reconcile_preclaim(OID,{"state":"not_running"}))

    def test_one_click_rejects_unapproved_and_unauthorized_requests(self):
        self.assertEqual(self.client.post("/admin/boletas/"+OID+"/emit").status_code,401)
        self.assertEqual(self.client.post("/admin/boletas/"+OID+"/emit",auth=self.auth).status_code,403)
        self.assertEqual(self.client.post("/admin/boletas/PSCP"+("F"*18)+"/emit",
                                          auth=self.auth,headers={"X-Billing-Action":"manual-invoice"}).status_code,409)
        self.assertEqual(self.client.get("/admin/boletas/"+OID+"/issue-status",auth=self.auth).status_code,200)

    def test_silent_preview_starts_chromium_without_tax_claim(self):
        with patch("sii_canary._server_request",return_value=(202,{"status":"starting"})) as request_start:
            response=self.client.post("/admin/boletas/"+OID+"/prepare-render",auth=self.auth,
                                      headers={"X-Billing-Action":"manual-invoice"})
        self.assertEqual(response.status_code,202,response.text)
        self.assertFalse(response.json["issuance"])
        args=request_start.call_args.args
        self.assertTrue(args[0].endswith("/api/internal/start"))
        from sii_canary import ticket_data
        import base64,json
        unsigned=args[2]["ticket"].split(".")[0]
        doc=json.loads(base64.urlsafe_b64decode(unsigned+"="*(-len(unsigned)%4)))
        self.assertEqual(doc["mode"],"preview")
        self.assertIsNone(ticket_data(args[2]["ticket"]))
        with billing.db() as con:
            import sii_canary
            self.assertFalse(sii_canary.any_claim(con))
            self.assertEqual(con.execute("SELECT count(*) FROM sii_issuance_attempts").fetchone()[0],0)
            self.assertEqual(con.execute("SELECT invoice_status FROM billing_orders WHERE order_id=?",(OID,)).fetchone()[0],"pending")

    def test_preview_503_bridge_error_is_not_misreported_as_busy(self):
        from urllib.error import HTTPError
        with patch("sii_canary._server_request",
                   side_effect=HTTPError("https://psc-sii-visual-test.onrender.com/api/internal/start",
                                         503,"unavailable",{},None)):
            r=self.client.post("/admin/boletas/"+OID+"/prepare-render",
                auth=self.auth,headers={"X-Billing-Action":"manual-invoice"})
        self.assertEqual(r.status_code,503)
        self.assertIn("no se emitió",r.json["error"].lower())
        self.assertNotIn("ocupado",r.json["error"].lower())

    def test_preview_rejects_other_active_work_without_false_issuance(self):
        from urllib.error import HTTPError
        with patch("sii_canary._server_request",
                   side_effect=HTTPError("https://psc-sii-visual-test.onrender.com/api/internal/start",
                                         409,"already running",{},None)):
            r=self.client.post("/admin/boletas/"+OID+"/prepare-render",
                auth=self.auth,headers={"X-Billing-Action":"manual-invoice"})
        self.assertEqual(r.status_code,409)
        self.assertIn("operación SII",r.json["error"])

    def test_preview_status_reports_exact_safe_chromium_stage(self):
        ready={"state":"prepared","stage":"prepared_not_issued","preparation":"prepared_not_issued"}
        with patch("sii_canary._server_request",return_value=(200,ready)):
            response=self.client.get("/admin/boletas/"+OID+"/prepare-status",auth=self.auth)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json["stage"],"prepared_not_issued")
        self.assertEqual(self.client.get("/admin/boletas/"+OID+"/prepare-status").status_code,401)
        self.assertEqual(self.client.post("/admin/boletas/"+OID+"/prepare-render",auth=self.auth).status_code,403)

    def test_folio_survives_download_and_progress_continues_until_company_mail_finishes(self):
        self.assertEqual(self.claim().status_code,200)
        snapshot={"folio":"716","dte":41,"amount":30000,"pdf":None}
        endpoint="/admin/sii-visual/internal-canary-complete"
        with patch("sii_receipt_admin.dispatch_psc",return_value="accepted") as dispatch:
            first=self.client.post(endpoint,json={"ticket":self.ticket(),"snapshot":snapshot},headers=self.bridge)
            self.assertTrue(first.json["folio_recorded"])
            self.assertFalse(first.json["pdf_stored"])
            dispatch.assert_not_called()
            active={"state":"open","stage":"downloading_sii_pdf"}
            with patch("sii_canary._server_request",return_value=(200,active)):
                status=self.client.get("/admin/boletas/"+OID+"/issue-status",auth=self.auth)
            self.assertEqual(status.json["state"],"running")
            self.assertEqual(status.json["folio"],"716")
            snapshot["pdf"]=base64.b64encode(b'%PDF-1.4\n'+b'x'*120+b'\n%%EOF\n').decode()
            second=self.client.post(endpoint,json={"ticket":self.ticket(),"snapshot":snapshot},headers=self.bridge)
            self.assertTrue(second.json["pdf_stored"])
            dispatch.assert_called_once_with(OID)
        finished={"state":"open","stage":"canary_issued_pdf_saved_sii_accepted"}
        with patch("sii_canary._server_request",return_value=(200,finished)):
            status=self.client.get("/admin/boletas/"+OID+"/issue-status",auth=self.auth)
        self.assertEqual(status.json["state"],"issued")
        self.assertEqual(status.json["stage"],finished["stage"])
        with patch("sii_canary._server_request",side_effect=TimeoutError):
            status=self.client.get("/admin/boletas/"+OID+"/issue-status",auth=self.auth)
        self.assertEqual(status.json["state"],"issued")

    def test_disabled_canary_stays_unavailable(self):
        with patch.dict(os.environ,{"SII_CANARY_ENABLED":"false"}):
            r=self.client.post("/admin/sii-visual/session",auth=self.auth,
                data={"order_id":OID,"mode":"issue_canary"})
            self.assertEqual(r.status_code,503)
            self.assertEqual(self.claim().status_code,403)


if __name__=="__main__":unittest.main()

