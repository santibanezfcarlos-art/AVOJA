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
    def evaluate(self,script,**kwargs):
        self.evaluations+=1
        if "__pscPermitFinal" in script:
            return None
        return next(self.results)
    def locator(self,selector):
        assert selector=="[data-psc-issue-final=yes]"
        return FakeNode(self)
    def wait_for_timeout(self,ms):
        return None


class Canaries(unittest.TestCase):
    def test_click_once_after_durable_claim(self):
        p=FakePage([{"ok":True,"amount":30000,"form_id":"btn","text_length":100},
                    {"ok":True,"amount":30000,"form_id":"btn","text_length":100}])
        with patch("issue_canary.claim",return_value=True) as claim:
            status=issue_canary.issue_once(p,ORDER,IDS,"signed-opaque")
        self.assertEqual(status,"canary_clicked_reconciliation")
        claim.assert_called_once_with("signed-opaque")
        self.assertEqual(p.clicks,1)
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
