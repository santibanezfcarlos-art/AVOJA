"""Send official SII receipt email once AFTER folio/PDF are recorded in PSC.

Existing SII UI can differ between releases. If there is no unique verified
mail control we do not claim or click it. Once claimed, uncertainty is recorded
and automated resending is forbidden.
"""
import json
import os
import re
import urllib.request
from urllib.parse import urlparse

from issue_canary import official_snapshot


def _api(ticket, folio, action, outcome=None, recipient=None):
    base=os.environ.get("SII_CREDENTIALS_API_URL","").rstrip("/")
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    host=urlparse(base)
    if host.scheme!="https" or not (host.hostname or "").endswith(".onrender.com") or len(secret)<32:
        raise RuntimeError("sii_email_bridge_unavailable")
    body={"ticket":ticket,"folio":folio,"action":action}
    if recipient is not None:
        body["recipient"]=recipient
    if outcome is not None:
        body["outcome"]=outcome
    raw=json.dumps(body,separators=(",",":")).encode()
    req=urllib.request.Request(
        base+"/admin/sii-visual/internal-sii-delivery",data=raw,
        headers={"Content-Type":"application/json","X-PSC-Visual-Bridge":secret},
        method="POST")
    with urllib.request.urlopen(req,timeout=12) as response:
        return json.loads(response.read(4096))


def _verified_email_controls(page, recipient):
    if urlparse(page.url).hostname!="eboleta.sii.cl":
        raise ValueError("unexpected_sii_host")
    email=page.get_by_label("E-mail",exact=True)
    if email.count()!=1 or not email.is_visible():
        raise ValueError("sii_mail_address_field_missing")
    if not isinstance(recipient,str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient):
        raise ValueError("invalid_recipient")
    email.fill(recipient)
    email.press("Tab")
    if email.input_value()!=recipient:
        raise ValueError("sii_mail_address_unverified")
    form=email.locator("xpath=ancestor::form[1]")
    button=form.get_by_role("button")
    if button.count()!=1 or not button.is_visible() or not button.is_enabled():
        raise ValueError("sii_mail_button_ambiguous")
    return button


def send_once(page, ticket, recipient, folio, amount):
    """Return accepted/unknown/blocked; no blind retry or second email click."""
    snapshot=official_snapshot(page,amount)
    if not snapshot or snapshot.get("folio")!=folio:
        return "blocked_document_unverified"
    try:
        button=_verified_email_controls(page,recipient)
    except (ValueError,AttributeError):
        return "blocked_mail_controls"
    # Durable claim must happen before any external mail effect.
    try:
        claim=_api(ticket,folio,"claim",recipient=recipient)
    except Exception:
        return "blocked_claim"
    if claim.get("claimed") is not True or claim.get("recipient")!=recipient:
        return "blocked_claim"
    outcome="unknown"
    try:
        def matching(response):
            try:
                req=response.request
                data=req.post_data_json
                return (req.method=="POST" and isinstance(data,dict)
                        and data.get("to")==recipient
                        and str(data.get("folio"))==folio
                        and int(data.get("dte",0))==41
                        and data.get("monto")==amount)
            except Exception:
                return False
        with page.expect_response(matching,timeout=30000) as waiting:
            button.click(timeout=10000)
        if waiting.value.ok:
            page.locator(".v-snack--active .success").wait_for(state="visible",timeout=10000)
            outcome="accepted"
    except Exception:
        # After click, timeout or navigation error may mean mail was sent.
        outcome="unknown"
    try:
        response=_api(ticket,folio,"complete",outcome)
        return response.get("state","unknown")
    except Exception:
        return "unknown"

