"""One irreversible SII click, only after administrator confirmed a canary.

No retry after claim. A result without a verified official folio remains in
'reconciliation': never fabricate issue success from a click or HTTP status.
"""
import json
import os
import time
import re
import urllib.request
from urllib.parse import urlparse


def verify(page, ids, order):
    if urlparse(page.url).hostname != "eboleta.sii.cl" or set(ids) != {
        "rut", "nombre", "direccion", "email", "telefono", "detalle"
    }:
        raise ValueError("unexpected_final_form")
    result=page.evaluate(r"""({ids,expected}) => {
      const el=Object.fromEntries(Object.entries(ids).map(([k,id])=>[k,document.getElementById(id)]));
      const visible=e=>e && e.getBoundingClientRect().width>0 && e.getBoundingClientRect().height>0;
      const normalize=(s)=>String(s||'').replace(/[.\s-]/g,'').toUpperCase();
      for(const [k,e] of Object.entries(el)){
        if(!visible(e) || !e.checkValidity() || e.getAttribute('aria-invalid')==='true')return {ok:false,reason:'field_invalid'};
        if(k==='rut' ? normalize(e.value)!==normalize(expected[k]) : e.value!==expected[k])
          return {ok:false,reason:'field_mismatch'};
      }
      let node=el.rut, scope=null;
      while(node && node!==document.body){
        if(Object.values(el).every(e=>node.contains(e))){
          scope=node;break;
        }
        node=node.parentElement;
      }
      if(!scope)return {ok:false,reason:'no_common_form'};
      const controls=[...scope.querySelectorAll('button,[role=button],input[type=button],input[type=submit]')];
      const matches=controls.filter(e=>visible(e)&&/^\s*emitir(?:\s+boleta)?\s*$/i.test((e.innerText||e.value||'').trim()));
      if(matches.length!==1 || matches[0].disabled || matches[0].getAttribute('aria-disabled')==='true')
        return {ok:false,reason:'final_button_ambiguous'};
      const text=scope.innerText;
      if(!/boleta[^\n]{0,80}(exenta|no afecta)/i.test(text))
        return {ok:false,reason:'dte41_not_confirmed'};
      const amounts=[...text.matchAll(/\$\s*([0-9][0-9.]*)/g)].map(m=>Number(m[1].replaceAll('.','')));
      if(amounts.length!==1 || amounts[0]!==expected.amount)
        return {ok:false,reason:'amount_not_confirmed'};
      matches[0].setAttribute('data-psc-issue-final','yes');
      return {ok:true,amount:amounts[0],form_id:matches[0].getAttribute('id')||'',text_length:text.length};
    }""", {"ids":ids,"expected":order})
    if not result or result.get("ok") is not True:
        raise ValueError("final_unverified_"+str((result or {}).get("reason","unknown")))
    return result


def claim(ticket):
    base=os.environ.get("SII_CREDENTIALS_API_URL","").rstrip("/")
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    if not base.startswith("https://") or len(secret)<32:
        raise RuntimeError("claim_transport_not_configured")
    parsed=urlparse(base)
    if not parsed.hostname or not parsed.hostname.endswith(".onrender.com"):
        raise RuntimeError("claim_host_rejected")
    payload=json.dumps({"ticket":ticket},separators=(',',':')).encode()
    req=urllib.request.Request(
        base+"/admin/sii-visual/internal-canary-claim",data=payload,
        headers={"Content-Type":"application/json","X-PSC-Visual-Bridge":secret},
        method="POST",
    )
    with urllib.request.urlopen(req,timeout=12) as response:
        data=json.loads(response.read(2048))
    if data.get("claimed") is not True:
        raise RuntimeError("claim_not_confirmed")
    return True



def official_snapshot(page, expected_amount):
    """Read SII's emitted Vue component, not the input form or a generic HTTP 200."""
    if urlparse(page.url).hostname!="eboleta.sii.cl":
        return None
    data=page.evaluate("""() => {
      const components=new Set();
      for(const el of document.querySelectorAll('*')){
        const vm=el.__vue__;
        if(vm && vm.emitido===true && vm.compartirData && vm.compartirData.folio){
          components.add(vm);
        }
      }
      if(components.size!==1)return null;
      const vm=[...components][0], d=vm.compartirData;
      return {folio:String(d.folio),dte:Number(d.dte),amount:Number(d.monto),
              pdf:vm.b64Pdf||null,pdf_url:vm.urlPdf||null};
    }""")
    if not isinstance(data,dict):
        return None
    if (data.get("dte")!=41 or type(data.get("amount")) is not int
        or data["amount"]!=expected_amount
        or not re.fullmatch(r"[1-9][0-9]{0,9}",str(data.get("folio","")))):
        return None
    return data


def report_complete(ticket, snapshot):
    base=os.environ.get("SII_CREDENTIALS_API_URL","").rstrip("/")
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    if not base.startswith("https://") or len(secret)<32:
        raise RuntimeError("complete_transport_not_configured")
    if not (urlparse(base).hostname or "").endswith(".onrender.com"):
        raise RuntimeError("complete_host_rejected")
    payload=json.dumps({"ticket":ticket,"snapshot":snapshot},separators=(',',':')).encode()
    if len(payload)>7_100_000:
        raise RuntimeError("official_pdf_too_large")
    req=urllib.request.Request(
        base+"/admin/sii-visual/internal-canary-complete",data=payload,
        headers={"Content-Type":"application/json","X-PSC-Visual-Bridge":secret},
        method="POST")
    with urllib.request.urlopen(req,timeout=35) as response:
        data=json.loads(response.read(4096))
    if data.get("state")!="issued" or data.get("folio_recorded") is not True:
        raise RuntimeError("official_record_not_confirmed")
    return data



def wait_for_official_receipt(page, expected_amount, *, timeout_seconds=90,
                              poll_interval_ms=1500):
    """Wait up to 90 seconds for the existing SII receipt screen.

    This function is READ-ONLY: no form submit, no second issue click.
    Transient DOM changes during SII navigation are normal, so keep polling.
    """
    deadline=time.monotonic()+timeout_seconds
    folio_only=None
    while True:
        try:
            snapshot=official_snapshot(page,expected_amount)
        except Exception:
            # The SII view may be navigating/re-rendering after submission.
            snapshot=None
        if snapshot:
            # The folio may appear before the PDF is ready. Prefer to keep
            # waiting for the PDF so it can be stored and emailed immediately.
            if snapshot.get("pdf_url") or snapshot.get("pdf"):
                return snapshot
            folio_only=snapshot
        remaining=deadline-time.monotonic()
        if remaining<=0:
            # Preserve a verified folio even if PDF generation was delayed.
            return folio_only
        page.wait_for_timeout(min(poll_interval_ms,max(1,int(remaining*1000))))



def issue_once(page, order, ids, ticket, on_submitted=None):
    """A verified claim survives crashes and forbids automated reissue."""
    def progress(stage):
        if on_submitted is not None:
            try:
                on_submitted(stage)
            except Exception:
                pass
    first=verify(page,ids,order)
    claim(ticket)
    try:
        second=verify(page,ids,order)
        if second!=first:
            raise RuntimeError("form_changed_after_claim")
        # The document listener permits only the previously verified exact node.
        page.evaluate("""() => {
          const b=document.querySelector('[data-psc-issue-final=yes]');
          if(!b)throw Error('final_button_lost');
          window.__pscPermitFinal=b;
        }""")
        try:
            page.locator('[data-psc-issue-final=yes]').click(timeout=10000)
            # Update the administrator's inline status without triggering
            # another submit. No callback failure may cause a second click.
            progress("waiting_sii_receipt")
            # Exactly one SII click. The portal can take significantly longer
            # than the old ~19-second window to display the official receipt.
            # Keep waiting for up to 90 seconds, never press Emitir again.
            snapshot=wait_for_official_receipt(page,order["amount"],
                                               timeout_seconds=90)
            if snapshot:
                try:
                    # Persist the verified folio before any download can fail
                    # or the ephemeral browser can be stopped by Render.
                    report_complete(ticket,{**snapshot,"pdf":None})
                    from sii_receipt_download import download_official_pdf
                    progress("downloading_sii_pdf")
                    try:
                        snapshot["pdf"]=download_official_pdf(page,snapshot)
                    except Exception:
                        # Keep the CONFIRMED folio even if downloading fails.
                        # A PDF failure can never authorize a second issuance.
                        snapshot["pdf"]=None
                    if not snapshot["pdf"]:
                        return "canary_issued_pdf_pending"
                    result=report_complete(ticket,snapshot)
                    if not result.get("pdf_stored"):
                        return "canary_issued_pdf_pending"
                    # After PSC persists folio and PDF (and dispatches the
                    # corporate email), the same official SII page can send
                    # its original receipt mail once. A failed/ambiguous
                    # send never triggers another issuance or a mail retry.
                    from sii_email_after_issue import send_once
                    try:
                        progress("sending_sii_company_email")
                        state=send_once(page,ticket,result.get("sii_recipient"),
                                        snapshot["folio"],order["amount"])
                    except Exception:
                        state="unknown"
                    return "canary_issued_pdf_saved_sii_"+state
                except Exception:
                    return "canary_issued_report_reconciliation"
            return "canary_clicked_reconciliation"
        finally:
            try:
                page.evaluate("() => {window.__pscPermitFinal=null}")
            except Exception:
                pass
    except Exception:
        # An issued document could exist despite any browser error. Never retry.
        return "canary_attempt_reconciliation"

