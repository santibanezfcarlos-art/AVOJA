"""Offline SII canary verification: no browser navigation and no tax click."""
import unittest
from unittest.mock import patch

import issue_canary

ORDER={
    "order_id":"PSCP"+"C"*18,
    "rut":"12345678-5","nombre":"Giovanni Ejemplo",
    "direccion":"Dirección ficticia 123","email":"example@example.invalid",
    "telefono":"+56912345678","detalle":"Sesión de psicoterapia breve online",
    "amount":30000
}
IDS={k:"fixture-"+k for k in ("rut","nombre","direccion","email","telefono","detalle")}


class FakeNode:
    def __init__(self,page):
        self.page=page
    def click(self,timeout=0):
        self.page.clicks+=1


class FakePage:
    url="https://eboleta.sii.cl/emitir/"
    def __init__(self,results):
        self.results=iter(results)
        self.clicks=0
        self.evaluations=0
    def evaluate(self,script,*args,**kwargs):
        self.evaluations+=1
        if "__pscPermitFinal" in script:
            return None
        return next(self.results,None)
    def locator(self,selector):
        assert selector=="[data-psc-issue-final=yes]"
        return FakeNode(self)
    def wait_for_timeout(self,ms):
        return None


class Canaries(unittest.TestCase):
    def test_click_once_after_durable_claim(self):
        p=FakePage([{"ok":True,"amount":30000,"form_id":"btn","text_length":100},
                    {"ok":True,"amount":30000,"form_id":"btn","text_length":100}])
        with (patch("issue_canary.claim",return_value=True) as claim,
              patch("issue_canary.wait_for_official_receipt",return_value=None) as waiting):
            status=issue_canary.issue_once(p,ORDER,IDS,"signed-opaque")
        waiting.assert_called_once_with(p,30000,timeout_seconds=90)
        self.assertEqual(status,"canary_clicked_reconciliation")
        claim.assert_called_once_with("signed-opaque")
        self.assertEqual(p.clicks,1)
    def test_official_result_is_reported_once_and_pdf_saved(self):
        page=FakePage([{"ok":True,"amount":30000,"form_id":"btn","text_length":100},
                       {"ok":True,"amount":30000,"form_id":"btn","text_length":100},
                       {"folio":"615","dte":41,"amount":30000,"pdf":"fake-b64-pdf"}])
        with (patch("issue_canary.claim",return_value=True),
              patch("issue_canary.report_complete",return_value={"state":"issued","pdf_stored":True}) as send,
              patch("issue_canary.wait_for_official_receipt",return_value={"folio":"615","dte":41,"amount":30000,"pdf":"fake-b64-pdf"}) as waiting,
              patch("sii_email_after_issue.send_once",return_value="accepted") as sii_mail):
            result=issue_canary.issue_once(page,ORDER,IDS,"signed-canary-ticket")
        self.assertEqual(result,"canary_issued_pdf_saved_sii_accepted")
        sii_mail.assert_called_once_with(page,"signed-canary-ticket",ORDER["email"],"615",30000)
        self.assertEqual(page.clicks,1)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1]["folio"],"615")

    def test_waits_full_ninety_seconds_without_reclicking(self):
        class Clock:
            now=1000.0
        clock=Clock()
        class TimedPage(FakePage):
            def __init__(self):
                super().__init__([])
                self.waits=[]
            def wait_for_timeout(self,ms):
                self.waits.append(ms)
                clock.now+=ms/1000.0
        page=TimedPage()
        with patch("issue_canary.time.monotonic",side_effect=lambda:clock.now):
            receipt=issue_canary.wait_for_official_receipt(page,30000)
        self.assertIsNone(receipt)
        self.assertGreaterEqual(clock.now-1000.0,89.99)
        self.assertLessEqual(clock.now-1000.0,90.02)
        self.assertTrue(all(0<ms<=1500 for ms in page.waits))
        self.assertEqual(page.clicks,0)

    def test_receipt_seen_early_returns_without_waiting_more(self):
        page=FakePage([])
        with patch("issue_canary.official_snapshot",side_effect=[None,None,{"folio":"615","dte":41,"amount":30000}]):
            result=issue_canary.wait_for_official_receipt(page,30000)
        self.assertEqual(result["folio"],"615")
        self.assertEqual(page.clicks,0)

    def test_transient_sii_dom_error_is_tolerated_until_receipt_appears(self):
        page=FakePage([])
        with patch("issue_canary.official_snapshot",
                   side_effect=[RuntimeError("navigation"),None,{"folio":"615","amount":30000}]):
            result=issue_canary.wait_for_official_receipt(page,30000)
        self.assertEqual(result["folio"],"615")

    def test_final_button_match_is_limited_to_emitir_or_emitir_boleta(self):
        from pathlib import Path
        source=Path(__file__).with_name('issue_canary.py').read_text(encoding='utf-8')
        self.assertIn(r'emitir(?:\s+boleta)?',source)
        self.assertNotIn(r'emitir.*',source)

    def test_never_clicks_if_any_form_check_fails(self):
        p=FakePage([{"ok":False,"reason":"amount_not_confirmed"}])
        with patch("issue_canary.claim") as claim:
            with self.assertRaises(ValueError):
                issue_canary.issue_once(p,ORDER,IDS,"token")
        claim.assert_not_called()
        self.assertEqual(p.clicks,0)
    def test_failure_claim_blocks_real_click(self):
        p=FakePage([{"ok":True,"amount":30000,"form_id":"btn","text_length":100}])
        with patch("issue_canary.claim",side_effect=RuntimeError("not_configured")):
            with self.assertRaises(RuntimeError):
                issue_canary.issue_once(p,ORDER,IDS,"token")
        self.assertEqual(p.clicks,0)
    def test_changed_fields_after_claim_never_click(self):
        p=FakePage([{"ok":True,"amount":30000,"form_id":"btn","text_length":100},
                    {"ok":True,"amount":30000,"form_id":"changed","text_length":100}])
        with patch("issue_canary.claim",return_value=True):
            state=issue_canary.issue_once(p,ORDER,IDS,"token")
        self.assertEqual(state,"canary_attempt_reconciliation")
        self.assertEqual(p.clicks,0)

if __name__=="__main__":unittest.main()
