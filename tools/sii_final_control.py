"""Fail-closed final-step guard. Does not infer success from an HTTP 200 or click."""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse


def save_result(path, status, **extra):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump({'status': status, 'emitted': False, **extra}, f)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def inspect_final(page, ids, data):
    if urlparse(page.url).hostname != 'eboleta.sii.cl':
        raise RuntimeError('session_required')
    if set(ids) != {'rut','nombre','direccion','email','telefono','detalle'}:
        raise RuntimeError('incomplete_fields')
    # The same DOM nodes must still hold all values AFTER all React blur handlers.
    info = page.evaluate(r'''({ids, expected}) => {
      const fields = Object.entries(ids).map(([key,id]) => [key,document.getElementById(id)]);
      const visible = e => e && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().height > 0;
      if (fields.some(([k,e]) => !visible(e) || e.value !== expected[k] || !e.checkValidity() || e.getAttribute('aria-invalid') === 'true'))
        return {valid:false, reason:'fields_changed'};
      const isEmit = e => visible(e) && (e.value || e.innerText || '').trim().toLowerCase() === 'emitir';
      const controls = 'button,[role="button"],input[type="button"],input[type="submit"]';
      const commonForm = fields[0][1].form;
      let scope = null;
      let button = null;
      if (commonForm && fields.every(([k,e]) => e.form === commonForm)) {
        const matches = Array.from(document.querySelectorAll(controls)).filter(e => e.form === commonForm && isEmit(e));
        if (matches.length === 1) { scope=commonForm; button=matches[0]; }
      }
      // React often uses a div instead of a native form: require the smallest
      // shared container of all six inputs, one Emitir and no calculator keys.
      if (!scope) {
        let parent = fields[0][1].parentElement;
        while (parent && parent !== document.body) {
          if (fields.every(([k,e]) => parent.contains(e))) {
            const matches=Array.from(parent.querySelectorAll(controls)).filter(isEmit);
            const digits=Array.from(parent.querySelectorAll(controls)).filter(e => visible(e) && /^[0-9]$/.test((e.innerText || e.value || '').trim()));
            if (matches.length === 1 && digits.length === 0) {scope=parent; button=matches[0];}
            break;
          }
          parent=parent.parentElement;
        }
      }
      if (!scope || !button) return {valid:false,reason:'ambiguous_form'};
      if (button.disabled || button.getAttribute('aria-disabled') === 'true') return {valid:false,reason:'disabled'};
      document.querySelectorAll('[data-psc-final]').forEach(e=>e.removeAttribute('data-psc-final'));
      button.setAttribute('data-psc-final','verified');
      const text=scope.innerText;
      return {valid:true, native_form:scope.tagName === 'FORM',
        amounts:Array.from(text.matchAll(/\$\s*([0-9][0-9.]*(?:,[0-9]+)?)/g),m=>m[1]),
        exempt:/boleta[^\n]{0,60}(exenta|no afecta)/i.test(text)};
    }''', {'ids':ids, 'expected':data})
    return info


def finish(page, ids, data, amount, result_path=None, authorize_order=None, journal=None):
    info = inspect_final(page, ids, data)
    print('Asociación estructural con formulario:', 'SÍ' if info.get('valid') else 'NO')
    if not info.get('valid'):
        save_result(result_path, 'blocked', reason=info.get('reason','unknown'))
        return
    if not authorize_order:
        save_result(result_path, 'prepared')
        print('Formulario verificado. No se pulsó el botón final.')
        return
    if data.get('order_id') != authorize_order or not journal:
        raise RuntimeError('authorization_mismatch')
    # Require DTE 41 context and a single unambiguous amount inside the same form.
    amounts = {int(s.replace('.', '')) for s in info.get('amounts',[]) if ',' not in s}
    if not info.get('exempt') or amounts != {amount}:
        save_result(result_path, 'blocked', reason='document_or_total_unverified')
        print('Falta confirmar tipo exento y monto dentro del formulario. No se emite.')
        return
    journal = Path(journal)
    journal.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(authorize_order.encode()).hexdigest()
    # Exclusive durable marker BEFORE clicking: crashes and timeouts never retry.
    with (journal / (key + '.attempt')).open('x', encoding='utf-8') as f:
        json.dump({'order_id':authorize_order, 'state':'reconciliation'}, f)
        f.flush()
        os.fsync(f.fileno())
    save_result(result_path, 'reconciliation', emitted=None)
    statuses = []
    def observe(response):
        if urlparse(response.url).hostname == 'eboleta.sii.cl' and response.request.method == 'POST':
            statuses.append(response.status)
    page.on('response', observe)
    try:
        # Re-evaluate every field immediately before the consequential action.
        checked = inspect_final(page, ids, data)
        if checked != info:
            raise RuntimeError('form_changed')
        page.locator('[data-psc-final="verified"]').click(timeout=10000)
        page.wait_for_timeout(8000)
    finally:
        # Until the real receipt schema is inspected, never fabricate a folio or
        # classify success. Only sanitized transport metadata is retained.
        save_result(result_path, 'reconciliation', emitted=None, response_statuses=statuses[:20])
        print('Resultado pendiente de conciliación en SII. NO volver a emitir esta compra.')
