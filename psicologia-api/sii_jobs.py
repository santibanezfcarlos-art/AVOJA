"""Authenticated outbound local-agent queue. This release only prepares forms.

A claimed job is never reclaimed automatically. Tokens expire in 12 hours and
are stored hashed. No SII credentials or browser profiles leave the local PC.
"""
import hashlib
import hmac
import secrets
import time
from functools import wraps

from flask import Blueprint, jsonify, request
import billing

bp = Blueprint('sii_jobs', __name__)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS sii_agent_tokens (
 digest TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sii_jobs (
 id TEXT PRIMARY KEY, order_id TEXT NOT NULL, mode TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 owner TEXT, result_code TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS sii_one_open_job ON sii_jobs(order_id)
 WHERE status IN ('queued','running','reconciliation');
'''
RESULTS = {'prepared', 'blocked', 'session_required', 'failed'}


def schema(con):
    con.executescript(SCHEMA)


def busy(con, order):
    # Called by manual ledger writes too; table may not exist before first use.
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sii_jobs'").fetchone():
        return False
    return con.execute("SELECT 1 FROM sii_jobs WHERE order_id=? AND status IN ('queued','running','reconciliation')", (order,)).fetchone() is not None


def eligible(row):
    return bool(row and ((row['environment'] == 'production' and row['invoice_status'] == 'pending') or
                 (row['environment'] == 'integration' and row['invoice_status'] == 'test')) and
                row['payment_status'] == 'approved' and
                not row['folio'] and type(row['amount']) is int and row['amount'] > 0)


def agent(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        token = request.headers.get('Authorization', '')
        if not token.startswith('Bearer ') or len(token) > 256:
            return jsonify(error='Agente no autorizado'), 401
        digest = hashlib.sha256(token[7:].encode()).hexdigest()
        with billing.db() as con:
            schema(con)
            row = con.execute('SELECT digest FROM sii_agent_tokens WHERE digest=? AND expires>?', (digest, int(time.time()))).fetchone()
        if not row or not hmac.compare_digest(row['digest'], digest):
            return jsonify(error='Vuelve a conectar el agente con el acceso del panel.'), 401
        return view(digest, *args, **kwargs)
    return wrapped


@bp.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.post('/admin/boletas/sii-agent-token')
@billing.private
def token():
    value = secrets.token_urlsafe(48)
    with billing.db() as con:
        schema(con)
        con.execute('DELETE FROM sii_agent_tokens WHERE expires<=?', (int(time.time()),))
        con.execute('INSERT INTO sii_agent_tokens VALUES (?,?)',
                    (hashlib.sha256(value.encode()).hexdigest(), int(time.time()) + 43200))
    return jsonify(token=value, expires_in=43200)


@bp.post('/admin/boletas/<order>/sii-jobs')
@billing.private
def enqueue(order):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error='Solicitud inválida'), 400
    if body.get('mode', 'preview') != 'preview':
        return jsonify(error='Emisión definitiva aún no habilitada. Primero validar formulario y respuesta SII.'), 409
    with billing.db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM billing_orders WHERE order_id=?', (order,)).fetchone()
        if not eligible(row):
            return jsonify(error='Solo compras aprobadas sin folio: reales pendientes o simulaciones de integración.'), 409
        if busy(con, order):
            return jsonify(error='Esta compra ya tiene un trabajo activo; no se reenviará.'), 409
        # Validate existing data, never accept replacement patient data from client.
        try:
            billing.receiver({'billing_' + k: row[k] for k in ('rut','name','address','email','phone')})
            if not 1 <= len(row['detail'].strip()) <= 80:
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(error='Ficha incompleta o detalle superior a 80 caracteres.'), 409
        job = secrets.token_hex(16)
        con.execute('INSERT INTO sii_jobs VALUES (?,?,?,?,?,?,?,?)',
                    (job, order, 'preview', 'queued', billing.now(), billing.now(), None, None))
    return jsonify(job_id=job, status='queued', emitted=False), 201


@bp.get('/admin/boletas/sii-jobs')
@billing.private
def history():
    with billing.db() as con:
        schema(con)
        rows = con.execute('SELECT id,order_id,mode,status,created_at,updated_at,result_code FROM sii_jobs ORDER BY rowid DESC LIMIT 500').fetchall()
    return jsonify(jobs=[dict(r) for r in rows], final_enabled=False)


@bp.post('/api/sii-agent/claim')
@agent
def claim(owner):
    with billing.db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        # Serialize all work: one local browser/profile at a time, even with two agents.
        if con.execute("SELECT 1 FROM sii_jobs WHERE status='running'").fetchone():
            return jsonify(job=None)
        job = con.execute("SELECT * FROM sii_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not job:
            return jsonify(job=None)
        row = con.execute('SELECT * FROM billing_orders WHERE order_id=?', (job['order_id'],)).fetchone()
        if not eligible(row):
            con.execute("UPDATE sii_jobs SET status='blocked',updated_at=?,result_code='order_changed' WHERE id=?", (billing.now(),job['id']))
            return jsonify(job=None)
        con.execute("UPDATE sii_jobs SET status='running',owner=?,updated_at=? WHERE id=?", (owner,billing.now(),job['id']))
        payload = {'order_id':row['order_id'], 'monto':row['amount'], 'environment':row['environment'],
                   'rut':row['rut'], 'nombre':row['name'], 'direccion':row['address'],
                   'email':row['email'], 'telefono':row['phone'], 'detalle':row['detail']}
    return jsonify(job={'id':job['id'], 'mode':'preview', 'data':payload})


@bp.post('/api/sii-agent/jobs/<job>/result')
@agent
def result(owner, job):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error='Solicitud inválida'), 400
    code = body.get('status')
    if not isinstance(code, str) or code not in RESULTS or body.get('emitted') is not False:
        return jsonify(error='Resultado no permitido; no se aceptan folios sin evidencia validada.'), 400
    with billing.db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM sii_jobs WHERE id=?', (job,)).fetchone()
        if not row or row['owner'] != owner:
            return jsonify(error='Trabajo no asignado a este agente'), 409
        if row['status'] == code:
            return jsonify(ok=True)  # Safe callback retry; never repeat browser actions.
        if row['status'] != 'running':
            return jsonify(error='Trabajo ya cerrado'), 409
        con.execute('UPDATE sii_jobs SET status=?,result_code=?,updated_at=? WHERE id=?', (code,code,billing.now(),job))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                    (row['order_id'],'sii_preview_'+code,billing.now(),'local-agent'))
    return jsonify(ok=True)


@bp.post('/admin/boletas/sii-jobs/<job>/close')
@billing.private
def close(job):
    # Only preview jobs can be released manually, after stopping the local process.
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error='Solicitud inválida'), 400
    if body.get('agent_stopped') is not True:
        return jsonify(error='Detén el agente local antes de cerrar el trabajo.'), 400
    with billing.db() as con:
        schema(con)
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM sii_jobs WHERE id=?', (job,)).fetchone()
        if not row or row['mode'] != 'preview' or row['status'] not in ('queued','running'):
            return jsonify(error='No se puede cerrar este trabajo'), 409
        con.execute("UPDATE sii_jobs SET status='blocked',result_code='manually_closed',updated_at=? WHERE id=?", (billing.now(),job))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                    (row['order_id'],'sii_preview_closed',billing.now(),request.authorization.username))
    return jsonify(ok=True)
