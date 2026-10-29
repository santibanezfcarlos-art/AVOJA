"""Adapters for an ALREADY issued SII receipt. Never clicks EMITIR.

Not invoked by the preview flow. The future issuance coordinator must persist
the returned folio before downloading and must persist a delivery claim before
calling send_email_once. There is deliberately no retry or issuance function.
"""
import base64
from urllib.parse import urlparse


def issued_snapshot(page):
    if urlparse(page.url).hostname != 'eboleta.sii.cl':
        raise ValueError('unexpected_receipt_host')
    # Read only the actual emitted component, not text from the input form.
    data = page.evaluate('''() => {
      const found = new Set();
      for (const el of document.querySelectorAll('*')) {
        let vm = el.__vue__;
        if (vm && vm.emitido === true && vm.compartirData && vm.compartirData.folio)
          found.add(vm);
      }
      if (found.size !== 1) return null;
      const vm = [...found][0], d = vm.compartirData;
      return {folio:String(d.folio),dte:Number(d.dte),amount:Number(d.monto),
              pdf:vm.b64Pdf || null};
    }''')
    if (not data or data.get('dte') != 41 or not str(data.get('folio','')).isdigit()
            or not isinstance(data.get('amount'), int) or data['amount'] <= 0):
        raise ValueError('issued_receipt_not_recognized')
    return data


def downloaded_pdf(snapshot):
    """Same official PDF used by the portal's download/print buttons."""
    encoded = snapshot.get('pdf')
    if not isinstance(encoded,str) or len(encoded)>7_000_000:
        raise ValueError('pdf_not_ready')
    if encoded.startswith('data:application/pdf;base64,'):
        encoded = encoded.split(',',1)[1]
    data = base64.b64decode(encoded,validate=True)
    if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
        raise ValueError('invalid_pdf')
    return data


def complete_after_issue(page, order_id, issued_date, ledger, send_payload):
    """Future coordinator entrypoint; caller supplies already authorized session.

    A storage error propagates and stops delivery, without ever reissuing.
    Each channel returns its own result, so one failure does not stop the other.
    """
    data = issued_snapshot(page)
    ledger.record_issued(order_id,data['folio'],issued_date,data['amount'],data['dte'])
    ledger.store_pdf(order_id,downloaded_pdf(data))
    sii = ledger.deliver(order_id,'sii',lambda receipt,key:send_email_once(page,receipt,key))
    psc = ledger.deliver(order_id,'psc',ledger.psc_sender(send_payload))
    return {'folio_recorded':True,'pdf_stored':True,'sii':sii,'psc':psc}


def send_email_once(page, receipt, key):
    snapshot = issued_snapshot(page)
    # Automatic association to the previously bound purchase, no manual question.
    if snapshot['folio'] != receipt['folio'] or snapshot['amount'] != receipt['amount']:
        raise ValueError('receipt_changed')
    email = page.get_by_label('E-mail',exact=True)
    if email.count()!=1:
        raise ValueError('email_control_not_recognized')
    email.fill(receipt['recipient'])
    email.press('Tab')
    if email.input_value()!=receipt['recipient']:
        raise ValueError('email_not_filled')
    form = email.locator('xpath=ancestor::form[1]')
    button = form.get_by_role('button')
    if button.count()!=1 or not button.is_enabled():
        raise ValueError('send_control_not_recognized')
    # Await the response to the one email POST, matched to this folio/email.
    def matching(response):
        try:
            req=response.request
            payload=req.post_data_json
            return (req.method=='POST' and isinstance(payload,dict)
                    and payload.get('to')==receipt['recipient']
                    and str(payload.get('folio'))==receipt['folio']
                    and int(payload.get('dte',0))==41)
        except Exception:
            return False
    with page.expect_response(matching,timeout=30000) as pending:
        button.click(timeout=10000)
    response=pending.value
    if not response.ok:
        raise ValueError('sii_delivery_unconfirmed')
    # Portal marks successful HTTP response via notificarExito; await its success
    # notification as well. Lack of confirmation means unknown, never auto-resend.
    page.locator('.v-snack--active .success').wait_for(state='visible',timeout=10000)
    return 'sii-confirmed-'+receipt['folio']
