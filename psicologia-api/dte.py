"""Durable DTE outbox in the existing private billing database.

Only confirm_production_payment may enqueue a real document. It queries
Transbank server-to-server, never trusts browser payment flags. Not a Flask route.
"""
import base64
import hashlib
import json
import os
import re
import time
import uuid
import billing
from dte_provider import OpenFactura, ProviderError, Uncertain, Duplicate, payload, pdf_bytes

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dte_payment_sessions (
 order_id TEXT PRIMARY KEY REFERENCES billing_orders(order_id), session_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dte_verified_payments (
 order_id TEXT PRIMARY KEY REFERENCES billing_orders(order_id), verified_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dte_jobs (
 order_id TEXT PRIMARY KEY REFERENCES billing_orders(order_id),
 environment TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
 payload TEXT NOT NULL, verified_payment INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 first_attempt REAL, next_attempt REAL NOT NULL DEFAULT 0,
 lease_until REAL NOT NULL DEFAULT 0, claim TEXT,
 provider_token TEXT, folio TEXT, sii_status TEXT, pdf BLOB, xml TEXT,
 email_state TEXT NOT NULL DEFAULT 'pending', error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS dte_unique_folio ON dte_jobs(environment,folio)
 WHERE folio IS NOT NULL;
'''
SANDBOX_ISSUER = {'RUTEmisor':'76795561-8','RznSocEmisor':'HAULMER SPA',
 'GiroEmisor':'VENTA AL POR MENOR POR CORREO, POR INTERNET Y VIA TELEFONICA',
 'DirOrigen':'ARTURO PRAT 527 CURICO','CmnaOrigen':'Curicó','CdgSIISucur':'81303347'}


def audit(con, order, action):
    con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                (order, action, billing.now(), 'dte-worker'))


def bind_session(order, session_id):
    """Call from future server checkout after billing.prepare, BEFORE redirect."""
    if not isinstance(session_id, str) or not 8 <= len(session_id) <= 61:
        raise ValueError('Sesión inválida')
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT payment_status FROM billing_orders WHERE order_id=?', (order,)).fetchone()
        if not row or row['payment_status'] != 'initiated':
            raise ValueError('Orden inválida')
        old = con.execute('SELECT session_id FROM dte_payment_sessions WHERE order_id=?',(order,)).fetchone()
        if old and old['session_id'] != session_id:
            raise ValueError('La sesión ya está vinculada')
        con.execute('INSERT OR IGNORE INTO dte_payment_sessions VALUES (?,?)',(order,session_id))


def _enqueue(con, row, verified=False):
    if con.execute('SELECT 1 FROM dte_jobs WHERE order_id=?',(row['order_id'],)).fetchone():
        return
    if row['folio'] or row['invoice_status'] == 'issued':
        raise ValueError('Orden ya documentada')
    if row['environment'] == 'production':
        if not verified:
            raise ValueError('Pago real no verificado')
        issuer = json.loads(os.environ.get('PSC_DTE_ISSUER_JSON', '{}'))
        allowed = os.getenv('PSC_DTE_EXEMPT_SERVICES', '').split(',')
        if row['service'] not in allowed:
            raise ValueError('Exención del servicio pendiente de validación')
        document_row = dict(row)
    else:
        issuer = SANDBOX_ISSUER
        # Even a live sandbox exercise must never disclose patient data.
        document_row = dict(row, rut='66666666-6', name='PRUEBA SIN VALIDEZ TRIBUTARIA',
                            address='DIRECCION DE PRUEBA 123', phone='000000000',
                            detail='PRUEBA DTE 41 SIN VALIDEZ TRIBUTARIA')
    body = payload(document_row, issuer, billing.now()[:10])
    con.execute('''INSERT INTO dte_jobs
      (order_id,environment,idempotency_key,payload,verified_payment,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?)''', (row['order_id'],row['environment'],uuid.uuid4().hex,
      json.dumps(body,sort_keys=True),int(verified),billing.now(),billing.now()))
    audit(con,row['order_id'],'dte_queued')


def enqueue_simulation(order):
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM billing_orders WHERE order_id=?',(order,)).fetchone()
        if not row or row['environment'] != 'integration' or row['payment_status'] != 'approved':
            raise ValueError('Solo pagos simulados aprobados')
        _enqueue(con, row)


def confirm_production_payment(order, token):
    """Preparation hook for real checkout; never used by webpay_demo routes.

    Status query is safe after commit or reconciliation. Returns only after the
    payment + outbox are durable. A failure must be retried, not charged again.
    """
    from webpay_gateway import Gateway, GatewaySettings, is_authorized
    config = GatewaySettings.from_environment()
    if config.environment != 'production':
        raise ValueError('Se requiere Webpay productivo')
    with billing.db() as con:
        row = con.execute('''SELECT b.*,s.session_id FROM billing_orders b
          JOIN dte_payment_sessions s USING(order_id) WHERE order_id=?''',(order,)).fetchone()
    if (not row or row['environment'] != 'production' or
        row['token_hash'] != hashlib.sha256(token.encode()).hexdigest()):
        raise ValueError('Orden y token no coinciden')
    result = Gateway(config).status(token)
    if not is_authorized(result, expected_order=order, expected_session=row['session_id'],expected_amount=row['amount']):
        raise ValueError('Pago no autorizado o datos no coincidentes')
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        current = con.execute('SELECT * FROM billing_orders WHERE order_id=?',(order,)).fetchone()
        if current['token_hash'] != row['token_hash'] or current['amount'] != row['amount']:
            raise ValueError('La orden cambió durante la confirmación')
        if current['folio']:
            return  # Existing manually issued document must never be duplicated.
        con.execute("UPDATE billing_orders SET payment_status='approved',paid_at=COALESCE(paid_at,?),invoice_status='pending' WHERE order_id=?",(billing.now(),order))
        con.execute('INSERT OR IGNORE INTO dte_verified_payments VALUES (?,?)',(order,billing.now()))
        audit(con,order,'transbank_production_verified')
    # Payment receipt is durable even when issuer/service configuration is missing.
    # The worker recovers this enqueue intent after a crash or configuration repair.
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        current=con.execute('SELECT * FROM billing_orders WHERE order_id=?',(order,)).fetchone()
        _enqueue(con,current,verified=True)


def enqueue_verified_pending():
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        rows=con.execute("""SELECT b.* FROM billing_orders b JOIN dte_verified_payments v USING(order_id)
          LEFT JOIN dte_jobs j USING(order_id) WHERE j.order_id IS NULL AND b.folio IS NULL
          AND b.payment_status='approved' AND b.environment='production'""").fetchall()
        for row in rows:
            try:
                _enqueue(con,row,verified=True)
            except (ValueError,TypeError):
                continue  # Remains pending in private ledger, never touches reservations.



def _finish(order, claim, **values):
    with billing.db() as con:
        values.update(updated_at=billing.now(),lease_until=0)
        sets=','.join(k+'=?' for k in values)
        changed=con.execute('UPDATE dte_jobs SET '+sets+' WHERE order_id=? AND claim=?',
                            (*values.values(),order,claim)).rowcount
        if changed:
            audit(con,order,'dte_'+values.get('state','updated'))
            if values.get('state') in ('accepted','accepted_with_observations'):
                job=con.execute('SELECT environment,payload,folio FROM dte_jobs WHERE order_id=?',(order,)).fetchone()
                if job['environment']=='production':
                    day=json.loads(job['payload'])['dte']['Encabezado']['IdDoc']['FchEmis']
                    con.execute("UPDATE billing_orders SET invoice_status='issued',folio=?,issued_date=? WHERE order_id=? AND folio IS NULL",(job['folio'],day,order))


def process_one():
    mode=os.getenv('PSC_DTE_MODE','disabled')
    if mode not in ('integration','production'):
        return False
    if mode=='production': enqueue_verified_pending()
    provider=OpenFactura(mode)
    stamp=time.time()
    claim=uuid.uuid4().hex
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('''SELECT j.* FROM dte_jobs j JOIN billing_orders b USING(order_id)
          WHERE j.environment=? AND b.payment_status='approved' AND b.folio IS NULL
          AND j.state IN ('queued','retry','processing','submitted')
          AND j.next_attempt<=? AND j.lease_until<=?
          ORDER BY j.created_at LIMIT 1''',(mode,stamp,stamp)).fetchone()
        if not row:
            return False
        row=dict(row)
        if mode=='production' and not row['verified_payment']:
            raise ValueError('Pago sin verificación')
        # Haulmer only protects duplicate POSTs for 24h. Never retry beyond 23h.
        if (not row['provider_token'] and row['first_attempt'] is not None and
            stamp-row['first_attempt']>=23*3600):
            con.execute("UPDATE dte_jobs SET state='reconciliation',error='Ventana de idempotencia vencida' WHERE order_id=?",(row['order_id'],))
            audit(con,row['order_id'],'dte_reconciliation')
            return True
        con.execute('''UPDATE dte_jobs SET state='processing',claim=?,lease_until=?,
          attempts=attempts+1,first_attempt=COALESCE(first_attempt,?) WHERE order_id=?''',
          (claim,stamp+300,stamp,row['order_id']))
    order=row['order_id']
    try:
        token=row['provider_token']
        if not token:
            try:
                response=provider.issue(json.loads(row['payload']),row['idempotency_key'])
                token=response.get('TOKEN','')
            except Duplicate as duplicate:
                token=duplicate.token
            if not re.fullmatch(r'[a-fA-F0-9]{64}',token):
                raise Uncertain('Falta identificador del proveedor')
            # Persist token BEFORE retrieval; all subsequent retries are GET only.
            with billing.db() as con:
                con.execute('UPDATE dte_jobs SET provider_token=? WHERE order_id=? AND claim=?',(token,order,claim))
        document=provider.get(token,'json')['json']
        if isinstance(document,str): document=json.loads(document)
        head=document['Encabezado']
        expected=json.loads(row['payload'])['dte']['Encabezado']
        if (str(head['IdDoc']['TipoDTE'])!='41' or
            head['IdDoc']['FchEmis']!=expected['IdDoc']['FchEmis'] or
            str(head['Totales']['MntTotal'])!=str(expected['Totales']['MntTotal']) or
            head['Emisor']['RUTEmisor']!=expected['Emisor']['RUTEmisor'] or
            head['Receptor']['RUTRecep']!=expected['Receptor']['RUTRecep']):
            raise ProviderError('Documento no coincide con la orden')
        folio=str(head['IdDoc']['Folio'])
        if not re.fullmatch(r'[1-9][0-9]{0,9}',folio): raise Uncertain('Folio inválido')
        pdf=pdf_bytes(provider.get(token,'pdf')['pdf'])
        xml=provider.get(token,'xml')['xml']
        status=provider.get(token,'status')['estado']
        state={'Aceptado':'accepted','Aceptado con Reparo':'accepted_with_observations',
               'Rechazado':'rejected','Pendiente':'submitted'}.get(status,'submitted')
        _finish(order,claim,state=state,folio=folio,pdf=pdf,xml=xml,sii_status=status,
                error=None,next_attempt=stamp+300)
        return True
    except (Uncertain,OSError,KeyError,ValueError,TypeError):
        _finish(order,claim,state='retry',error='Respuesta pendiente de conciliación',
                next_attempt=stamp+min(3600,60*2**min(row['attempts'],6)))
    except ProviderError:
        _finish(order,claim,state='review',error='Revisar configuración o respuesta del proveedor')
    return True
