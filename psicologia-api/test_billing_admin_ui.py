"""Guard the PSC issue button: the tax portal must never open a new tab."""
from pathlib import Path
import re
import unittest

SCRIPT=Path(__file__).with_name("billing_admin.js")


class EmitButtonUiTests(unittest.TestCase):
    def test_real_issue_stays_in_private_admin(self):
        script=SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("window.open(",script)
        self.assertNotIn("eboleta.sii.cl/emitir/",script)
        self.assertNotIn("issueForm.target='_blank'",script)
        self.assertIn("fetch('/admin/boletas/'+encodeURIComponent(r.order_id)+'/emit'",script)
        self.assertIn("'X-Billing-Action':'manual-invoice'",script)
        self.assertIn("issueMessage(r.order_id,",script)
        self.assertIn("issueState.dataset.issueOrder=r.order_id",script)

    def test_emission_only_offered_on_paid_pending_orders(self):
        script=SCRIPT.read_text(encoding="utf-8")
        self.assertIn("r.payment_status==='approved'&&!r.folio",script)
        self.assertIn("r.environment==='production'&&r.invoice_status==='pending'",script)
        self.assertIn("r.issuance_state==='reconciliation'||r.issuance_state==='processing'",script)

    def test_emission_progress_never_clears_purchase_cards(self):
        script=SCRIPT.read_text(encoding="utf-8")
        poll=script.split("async function pollIssue(order){",1)[1].split("async function refresh(",1)[0]
        self.assertNotIn("await refresh();",poll)
        self.assertIn("issueMessage(order,message)",poll)
        self.assertIn("await refresh({quiet:true});",poll)
        refresh=script.split("async function refresh({quiet=false}={})",1)[1]
        self.assertNotIn("$('orders').replaceChildren();",refresh)
        self.assertIn("document.createDocumentFragment()",refresh)

    def test_90_second_sii_receipt_status_stays_inline(self):
        script=SCRIPT.read_text(encoding="utf-8")
        self.assertIn("waiting_sii_receipt:'Esperando comprobante oficial del SII (hasta 90 segundos)…'",script)
        self.assertIn("issueMessage(order,message)",script)
        self.assertIn("await refresh({quiet:true});",script)

    def test_preview_does_not_issue(self):
        script=SCRIPT.read_text(encoding="utf-8")
        self.assertIn("'/prepare-render'",script)
        self.assertIn("'Preparar en SII (sin emitir)'",script)

if __name__=="__main__":
    unittest.main()
