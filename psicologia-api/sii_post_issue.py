"""Post-issuance ledger and delivery. No issuance action or purchase trigger.

Writes/delivery require explicit activation. Preview is read-only. Transport and
SII browser callbacks are injected by the later purchase integration.
"""
import base64
import hashlib
import html
import os
import re
from datetime import date

import billing

SCHEMA = '''CREATE TABLE IF NOT EXISTS sii_receipts (
 order_id TEXT PRIMARY KEY, folio TEXT UNIQUE NOT NULL, issued_date TEXT NOT NULL,
 amount INTEGER NOT NULL, recipient TEXT NOT NULL, patient_name TEXT NOT NULL,
 detail TEXT NOT NULL, pdf BLOB, pdf_sha256 TEXT,
 sii_state TEXT NOT NULL DEFAULT 'pending', psc_state TEXT NOT NULL DEFAULT 'pending',
 sii_reference TEXT, psc_reference TEXT, updated_at TEXT NOT NULL);
'''


def _enabled():
    if os.environ.get('SII_POST_ISSUE_ENABLED') != 'true':
        raise RuntimeError('post_issue_disabled')


def validate_pdf(data):
    if (not isinstance(data, bytes) or not 100 <= len(data) <= 5_000_000
            or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]):
        raise ValueError('invalid_pdf')


def record_issued(order_id, folio, issued_date, amount, dte=41):
    """Persist folio BEFORE attempting PDF/mail. No user/person reconfirmation."""
    _enabled()
    if type(dte) is not int or dte != 41 or not re.fullmatch(r'[1-9][0-9]{0,9}', str(folio)):
        raise ValueError('invalid_receipt')
    date.fromisoformat(issued_date)
    if type(amount) is not int or amount <= 0:
        raise ValueError('invalid_amount')
    with billing.db() as con:
        con.executescript(SCHEMA)
        con.execute('BEGIN IMMEDIATE')
        order = con.execute('SELECT * FROM billing_orders WHERE order_id=?', (order_id,)).fetchone()
        if (not order or order['environment'] != 'production'
                or order['payment_status'] != 'approved' or order['amount'] != amount):
            raise ValueError('order_not_eligible')
        if order['invoice_status'] not in ('pending', 'issued'):
            raise ValueError('order_not_eligible')
        if order['folio'] and str(order['folio']) != str(folio):
            raise ValueError('folio_conflict')
        if order['invoice_status'] == 'issued' and order['issued_date'] != issued_date:
            raise ValueError('issued_date_conflict')
        existing = con.execute('SELECT * FROM sii_receipts WHERE order_id=?', (order_id,)).fetchone()
        if existing:
            if (existing['folio'], existing['issued_date'], existing['amount']) != (str(folio), issued_date, amount):
                raise ValueError('receipt_conflict')
            return
        con.execute('''INSERT INTO sii_receipts
            (order_id,folio,issued_date,amount,recipient,patient_name,detail,updated_at)
            VALUES (?,?,?,?,?,?,?,?)''',
            (order_id,str(folio),issued_date,amount,order['email'],order['name'],order['detail'],billing.now()))
        con.execute("UPDATE billing_orders SET invoice_status='issued',folio=?,issued_date=?,marked_at=? WHERE order_id=?",
                    (str(folio),issued_date,billing.now(),order_id))
        con.execute("UPDATE sii_issuance_queue SET status='issued',updated_at=? WHERE order_id=?",
                    (billing.now(),order_id))
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sii_first_canary'").fetchone():
            con.execute("UPDATE sii_first_canary SET state='issued' WHERE order_id=?", (order_id,))
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sii_issuance_attempts'").fetchone():
            con.execute("UPDATE sii_issuance_attempts SET state='issued' WHERE order_id=?", (order_id,))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                    (order_id,'sii_receipt_recorded',billing.now(),'sii_post_issue'))


def store_pdf(order_id, pdf):
    _enabled()
    validate_pdf(pdf)
    digest = hashlib.sha256(pdf).hexdigest()
    with billing.db() as con:
        con.executescript(SCHEMA)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT pdf_sha256 FROM sii_receipts WHERE order_id=?',(order_id,)).fetchone()
        if not row:
            raise ValueError('folio_not_recorded')
        if row['pdf_sha256'] and row['pdf_sha256'] != digest:
            raise ValueError('pdf_conflict')
        con.execute('UPDATE sii_receipts SET pdf=?,pdf_sha256=?,updated_at=? WHERE order_id=?',
                    (pdf,digest,billing.now(),order_id))


def email_content(receipt, preview=False):
    """Corporate palette/layout shared with PSC reservation emails; escaped data."""
    esc = lambda value: html.escape(str(value), quote=True)
    folio = 'Por asignar al emitir' if preview else receipt['folio']
    amount = '$' + format(int(receipt['amount']), ',').replace(',', '.')
    name = str(receipt['patient_name']).strip()
    subject = 'Tu Boleta Electrónica | PSC Santibáñez'
    notice = "<p style='padding:12px;background:#fff2d9'>VISTA PREVIA · No se ha emitido ni enviado esta boleta.</p>" if preview else ''
    rows = ''.join("<tr><td style='padding:10px;color:#64796f'>"+esc(k)+"</td><td style='padding:10px'>"+esc(v)+"</td></tr>"
                   for k,v in [('Documento','Boleta exenta electrónica'),('Folio',folio),('Servicio',receipt['detail']),('Monto',amount)])
    body = f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f3f6f2;font-family:Arial,Helvetica,sans-serif;color:#173e32">
<table role="presentation" style="width:100%;padding:30px 12px"><tr><td align="center">
<table role="presentation" style="width:100%;max-width:590px;background:white;border:1px solid #dbe7df;border-radius:18px;overflow:hidden" cellspacing="0">
<tr><td style="background:#194a3a;color:white;padding:27px 30px"><strong style="font-size:22px">PSC Santibáñez</strong><div style="color:#d3e6db;margin-top:6px">Psicólogo Carlos Santibáñez Fica · Atención online</div></td></tr>
<tr><td style="padding:24px 30px">{notice}<div style="font-size:12px;color:#4d8268;letter-spacing:1px">DOCUMENTO DE TU COMPRA</div>
<h1 style="font-size:27px;color:#194a3a">Tu boleta electrónica</h1>
<p style="line-height:1.7">Estimado/a {esc(name)}:</p><p style="line-height:1.7">A continuación adjuntamos la boleta electrónica correspondiente al pago de tu servicio en PSC Santibáñez.</p>
<table role="presentation" style="width:100%;background:#f7faf7;border:1px solid #dce9df;border-radius:12px">{rows}</table>
<p style="line-height:1.7">Encontrarás el documento en formato PDF adjunto a este correo. Consérvalo como respaldo de tu compra.</p>
<p>Gracias por tu confianza.<br><strong>Ps. Carlos Santibáñez Fica</strong></p></td></tr>
<tr><td style="padding:20px 30px;background:#eef4ed;font-size:12px;color:#577166">contacto@pscsantibanez.cl · <a href="https://pscsantibanez.cl" style="color:#276b50">pscsantibanez.cl</a><br>Notificación automática de PSC Santibáñez.</td></tr>
</table></td></tr></table></body></html>'''
    plain = f'Estimado/a {name}:\nAdjuntamos tu boleta electrónica exenta N.º {folio}, por {amount}.\nPSC Santibáñez · contacto@pscsantibanez.cl'
    return subject, body, plain


def mail_payload(receipt):
    pdf = receipt['pdf']
    validate_pdf(pdf)
    subject, body, plain = email_content(receipt)
    return {'from':'Ps. Carlos Santibáñez F. <contacto@pscsantibanez.cl>',
            'to':[receipt['recipient']], 'subject':subject, 'html':body, 'text':plain,
            'attachments':[{'filename':'boleta-'+receipt['folio']+'.pdf',
                            'content':base64.b64encode(pdf).decode('ascii'), 'content_type':'application/pdf'}]}


def psc_sender(send_payload):
    """Bind the existing demo_notifications.send_payload corporate transport."""
    def send(receipt, key):
        return send_payload(mail_payload(receipt), key)
    return send


def deliver(order_id, channel, sender):
    """sender(receipt, stable_key) -> provider reference. Never issues a document.

    Persist 'sending' BEFORE external effects. An interrupted/ambiguous request
    becomes 'unknown' and is never blindly resent, even after process restart.
    A future reconciliation operation must explicitly resolve such attempts.
    """
    _enabled()
    if channel not in ('sii', 'psc'):
        raise ValueError('invalid_channel')
    col, ref = channel+'_state', channel+'_reference'
    with billing.db() as con:
        con.executescript(SCHEMA)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM sii_receipts WHERE order_id=?',(order_id,)).fetchone()
        if not row or not row['pdf']:
            raise ValueError('document_not_stored')
        if row[col] != 'pending':
            return row[col]
        con.execute(f'UPDATE sii_receipts SET {col}=?,updated_at=? WHERE order_id=?',('sending',billing.now(),order_id))
        receipt = dict(row)
    key = 'psc-receipt-'+hashlib.sha256((order_id+':'+receipt['folio']+':'+channel).encode()).hexdigest()
    try:
        reference = sender(receipt, key)
        if not isinstance(reference, str) or not reference or len(reference)>200:
            raise ValueError('unconfirmed_delivery')
    except Exception:
        with billing.db() as con:
            con.execute(f'UPDATE sii_receipts SET {col}=?,updated_at=? WHERE order_id=?',('unknown',billing.now(),order_id))
        return 'unknown'
    with billing.db() as con:
        con.execute(f'UPDATE sii_receipts SET {col}=?,{ref}=?,updated_at=? WHERE order_id=?',
                    ('accepted',reference,billing.now(),order_id))
    return 'accepted'
