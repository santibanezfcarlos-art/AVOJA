"""Original SII email: never click without an official receipt + durable claim."""
import unittest
from unittest.mock import patch
import sii_email_after_issue as email

class FakeButton:
    def __init__(self):self.clicks=0
    def click(self,timeout=None):self.clicks+=1

class FakeLocator:
    def __init__(self,button):self.button=button
    def get_by_role(self,label):return self.button

class FakeEmail:
    def __init__(self,button):
        self.button=button;self.value=""
    def count(self):return 1
    def is_visible(self):return True
    def fill(self,value):self.value=value
    def press(self,key):pass
    def input_value(self):return self.value
    def locator(self,xpath):return FakeLocator(self.button)

class FakePage:
    url="https://eboleta.sii.cl/emitir/"
    def __init__(self,button):
        self.email=FakeEmail(button)
    def get_by_label(self,label,exact):return self.email

class Button(FakeButton):
    def count(self):return 1
    def is_enabled(self):return True
    def is_visible(self):return True

class MailTests(unittest.TestCase):
    def test_unknown_official_folio_never_claims_or_sends(self):
        b=Button();page=FakePage(b)
        with (patch("sii_email_after_issue.official_snapshot",return_value=None),
              patch("sii_email_after_issue._api") as api):
            r=email.send_once(page,"ticket","client@example.invalid","615",30000)
        self.assertEqual(r,"blocked_document_unverified")
        api.assert_not_called()
        self.assertEqual(b.clicks,0)

    def test_one_sii_mail_click_after_claim_and_success_confirmation(self):
        button=Button()
        page=FakePage(button)
        class Response:
            ok=True
        class Pending:
            value=Response()
            def __enter__(self):return self
            def __exit__(self,*args):return False
        class Snackbar:
            def wait_for(self,**kwargs):return True
        page.expect_response=lambda predicate,timeout: Pending()
        page.locator=lambda selector: Snackbar()
        requests=[]
        def api(ticket,folio,action,outcome=None,recipient=None):
            requests.append((action,outcome))
            if action=="claim":return {"claimed":True,"recipient":"client@example.invalid"}
            return {"state":outcome}
        with (patch("sii_email_after_issue.official_snapshot",return_value={"folio":"615"}),
              patch("sii_email_after_issue._api",side_effect=api)):
            state=email.send_once(page,"ticket","client@example.invalid","615",30000)
        self.assertEqual(state,"accepted")
        self.assertEqual(requests,[("claim",None),("complete","accepted")])
        self.assertEqual(button.clicks,1)

    def test_missing_button_never_claims(self):
        class InvalidButton(Button):
            def count(self):return 0
        p=FakePage(InvalidButton())
        with (patch("sii_email_after_issue.official_snapshot",return_value={"folio":"615"}),
              patch("sii_email_after_issue._api") as api):
            r=email.send_once(p,"ticket","client@example.invalid","615",30000)
        self.assertEqual(r,"blocked_mail_controls")
        api.assert_not_called()

    def test_failed_claim_does_not_press_sii_mail_button(self):
        b=Button();p=FakePage(b)
        with (patch("sii_email_after_issue.official_snapshot",return_value={"folio":"615"}),
              patch("sii_email_after_issue._api",side_effect=TimeoutError)):
            r=email.send_once(p,"ticket","client@example.invalid","615",30000)
        self.assertEqual(r,"blocked_claim")
        self.assertEqual(b.clicks,0)

if __name__=="__main__":unittest.main()

