"""Private manual billing ledger. No SII issuance and no public patient lookup."""
import csv
import hashlib
import hmac
import io
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request, send_file
from werkzeug.security import check_password_hash

billing_bp = Blueprint('billing', __name__)
TZ = ZoneInfo('America/Santiago')
DETAILS = {'psicoterapia': 'Sesión de psicoterapia breve online',
           'empleabilidad': 'Asesoría en empleabilidad online',
           'competencias': 'Desarrollo de competencias online'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS billing_orders (
 order_id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, environment TEXT NOT NULL,
 created_at TEXT NOT NULL, paid_at TEXT, service TEXT NOT NULL,
 appointment_date TEXT NOT NULL, appointment_time TEXT NOT NULL,
 amount INTEGER NOT NULL, payment_method TEXT NOT NULL DEFAULT 'Webpay',
 rut TEXT NOT NULL, name TEXT NOT NULL, address TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL, detail TEXT NOT NULL,
 payment_status TEXT NOT NULL DEFAULT 'initiated',
 invoice_status TEXT NOT NULL DEFAULT 'not_applicable',
 folio TEXT, issued_date TEXT, marked_at TEXT);
 CREATE UNIQUE INDEX IF NOT EXISTS unique_invoice_folio
 ON billing_orders(folio) WHERE folio IS NOT NULL;
 CREATE TABLE IF NOT EXISTS billing_audit (
 id INTEGER PRIMARY KEY, order_id TEXT NOT NULL, action TEXT NOT NULL,
 created_at TEXT NOT NULL, actor TEXT NOT NULL);
'''


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def configured():
    path = os.environ.get('PSICOLOGIA_BILLING_DB', '')
    secret = os.environ.get('PSICOLOGIA_BILLING_ADMIN_HASH', '')
    password = os.environ.get('PSICOLOGIA_BILLING_ADMIN_PASSWORD', '')
    if not path or not Path(path).is_absolute() or not (secret.startswith(('scrypt:', 'pbkdf2:')) or len(password) >= 16):
        return False
    # Never silently create a ledger on Render's ephemeral filesystem.
    if os.environ.get('RENDER') and (not path.startswith('/var/data/') or not os.path.ismount('/var/data')):
        return False
    return Path(path).parent.is_dir()


@contextmanager
def db():
    if not configured():
        raise RuntimeError('Billing storage is not configured')
    con = sqlite3.connect(os.environ['PSICOLOGIA_BILLING_DB'], timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def receiver(form):
    values = {}
    for key, minimum, maximum in [('rut', 8, 12), ('name', 2, 120), ('address', 5, 200),
                                  ('email', 5, 254), ('phone', 8, 20)]:
        value = form.get('billing_' + key, '').strip()
        if not minimum <= len(value) <= maximum or any(ord(c) < 32 for c in value):
            raise ValueError('Completa correctamente RUT, nombre, dirección, correo y teléfono.')
        values[key] = value
    rut = values['rut'].replace('.', '').replace('-', '').upper()
    if not re.fullmatch(r'[1-9][0-9]{6,7}[0-9K]', rut):
        raise ValueError('RUT inválido.')
    total = sum(int(d) * (2 + i % 6) for i, d in enumerate(reversed(rut[:-1])))
    digit = '0123456789K'[(-total) % 11]
    if digit != rut[-1]:
        raise ValueError('Revisa el dígito verificador del RUT.')
    values['rut'] = rut[:-1] + '-' + rut[-1]
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        raise ValueError('Correo inválido.')
    if not re.fullmatch(r'\+?[0-9 ()-]+', values['phone']) or not 8 <= len(re.sub(r'\D', '', values['phone'])) <= 15:
        raise ValueError('Teléfono inválido.')
    return values


def prepare(order, service, day, hour, amount, person, environment, detail=None):
    if environment not in ('integration', 'production'):
        raise ValueError('Invalid billing environment')
    with db() as con:
        con.execute('''INSERT INTO billing_orders
         (order_id,environment,created_at,service,appointment_date,appointment_time,
          amount,rut,name,address,email,phone,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
         (order,environment,now(),service,day,hour,amount,person['rut'],person['name'],
          person['address'],person['email'],person['phone'],detail or DETAILS[service]))


def bind_token(order, token):
    with db() as con:
        con.execute('UPDATE billing_orders SET token_hash=? WHERE order_id=?',
                    (hashlib.sha256(token.encode()).hexdigest(), order))



def pending_by_token(token):
    """Recupera solo identificador/monto para liberar una compra abandonada."""
    if not configured() or not isinstance(token,str) or len(token)>150:
        return None
    try:
        with db() as con:
            row=con.execute(
                "SELECT order_id,amount FROM billing_orders WHERE token_hash=? "
                "AND environment='integration' AND payment_status='initiated'",
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return {"order":row["order_id"],"amount":row["amount"]} if row else None
    except (RuntimeError, sqlite3.Error, OSError):
        return None


def pending_by_order(order):
    """Read only pending sandbox orders; never expose personal information."""
    if not configured() or not isinstance(order, str) or not re.fullmatch(r'PSCD[0-9A-F]{18}', order):
        return None
    try:
        with db() as con:
            row = con.execute(
                "SELECT order_id, amount, service, appointment_date, appointment_time "
                "FROM billing_orders WHERE order_id=? AND environment='integration' "
                "AND payment_status='initiated'", (order,)).fetchone()
        return dict(row) if row else None
    except (RuntimeError, sqlite3.Error, OSError):
        return None


def cancel_pending_order(order):
    """Idempotent, conditional cancellation: never overwrite approved payments."""
    if not configured():
        raise RuntimeError('Billing storage is not configured')
    with db() as con:
        cur = con.execute(
            "UPDATE billing_orders SET payment_status='cancelled' "
            "WHERE order_id=? AND environment='integration' AND payment_status='initiated'",
            (order,))
        return cur.rowcount == 1


def cancelled(token):
    if not configured():
        return
    with db() as con:
        con.execute("UPDATE billing_orders SET payment_status='cancelled' WHERE token_hash=? AND payment_status='initiated'",
                    (hashlib.sha256(token.encode()).hexdigest(),))


def record_result(order, token, amount, approved, payment_type):
    if not configured():
        return False
    with db() as con:
        row = con.execute('SELECT * FROM billing_orders WHERE order_id=?', (order,)).fetchone()
        if row is None:
            return False  # Legacy test without receiver data.
        if row['amount'] != amount or row['token_hash'] != hashlib.sha256(token.encode()).hexdigest():
            raise ValueError('Billing transaction mismatch')
        if row['payment_status'] == 'approved':
            return True  # Idempotent, cannot downgrade or reset an issued invoice.
        invoice = ('test' if row['environment'] == 'integration' else 'pending') if approved else 'not_applicable'
        method = 'Tarjeta de débito' if payment_type == 'VD' else 'Tarjeta de prepago' if payment_type == 'VP' else 'Tarjeta de crédito'
        con.execute('''UPDATE billing_orders SET payment_status=?, invoice_status=?, paid_at=?, payment_method=?
                       WHERE order_id=?''', ('approved' if approved else 'rejected', invoice,
                       now() if approved else None, method, order))
    return True


def private(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not configured():
            return Response('Historial pendiente de configurar: almacenamiento persistente y acceso privado.', status=503)
        auth = request.authorization
        username = os.environ.get('PSICOLOGIA_BILLING_ADMIN_USER', 'carlos')
        secret = os.environ.get('PSICOLOGIA_BILLING_ADMIN_HASH', '')
        supplied = (auth.password or '') if auth else ''
        valid = (check_password_hash(secret, supplied) if secret.startswith(('scrypt:', 'pbkdf2:'))
                 else hmac.compare_digest(supplied.encode(), os.environ.get('PSICOLOGIA_BILLING_ADMIN_PASSWORD', '').encode()))
        if (not auth or auth.type != 'basic' or not hmac.compare_digest((auth.username or '').encode(), username.encode()) or not valid):
            return Response('Acceso privado', status=401, headers={'WWW-Authenticate': 'Basic realm="Boletas PSC", charset="UTF-8"'})
        if request.method == 'POST' and request.headers.get('X-Billing-Action') != 'manual-invoice':
            return jsonify(error='Solicitud no autorizada'), 403
        return view(*args, **kwargs)
    return wrapped


@billing_bp.after_request
def private_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    return response


@billing_bp.get('/api/billing/config')
def config():
    try:
        with db() as con:
            con.execute('SELECT 1')
        ready = True
    except (RuntimeError, sqlite3.Error, OSError):
        ready = False
    return jsonify(ready=ready)


@billing_bp.get('/admin/boletas')
@private
def panel():
    return send_file(Path(__file__).parent / 'billing_admin.html')


@billing_bp.get('/admin/boletas/<asset>')
@private
def assets(asset):
    if asset not in ('billing_admin.js', 'billing_admin.css', 'sii_login_test.js'):
        return '', 404
    return send_file(Path(__file__).parent / asset)


def filtered_rows():
    environment = request.args.get('environment', 'production')
    status = request.args.get('status', 'pending')
    if environment not in ('production', 'integration') or status not in ('pending','issued','test','all'):
        raise ValueError('Filtro inválido')
    sql = 'SELECT * FROM billing_orders WHERE environment=?'
    params = [environment]
    if status != 'all':
        sql += ' AND invoice_status=?'
        params.append(status)
    for key, operator in [('from', '>='), ('to', '<=')]:
        value = request.args.get(key, '')
        if value:
            datetime.strptime(value, '%Y-%m-%d')
            sql += ' AND substr(created_at,1,10)' + operator + '?'
            params.append(value)
    query = request.args.get('q', '').strip()[:120]
    if query:
        sql += ' AND (instr(lower(name),lower(?))>0 OR instr(rut,?)>0 OR instr(order_id,?)>0)'
        params.extend([query, query, query])
    with db() as con:
        rows = con.execute(sql + ' ORDER BY created_at DESC,order_id DESC', params).fetchall()
    return [{k:r[k] for k in r.keys() if k != 'token_hash'} for r in rows]


@billing_bp.get('/admin/boletas/data')
@private
def data():
    try:
        return jsonify(orders=filtered_rows())
    except ValueError:
        return jsonify(error='Filtro inválido'), 400


@billing_bp.post('/admin/boletas/sii-login-test')
@private
def sii_login_test():
    """Prueba de acceso SII controlada desde el panel privado. Nunca emite."""
    from sii_eboleta import authenticate_sii, SiiAutomationError
    try:
        result = authenticate_sii()
        return jsonify(result)
    except SiiAutomationError as exc:
        return jsonify(error=str(exc), emitted=False), 409


@billing_bp.post('/admin/boletas/<order>/sii-preview')
@private
def sii_preview(order):
    """Validate a server-side SII job without visiting SII or issuing a DTE."""
    if not re.fullmatch(r'PSCD[0-9A-F]{18}', order):
        return jsonify(error='Orden inválida'), 400
    with db() as con:
        row = con.execute(
            'SELECT order_id,amount,environment,payment_status,invoice_status '
            'FROM billing_orders WHERE order_id=?', (order,)).fetchone()
    if row is None:
        return jsonify(error='Compra no encontrada'), 404
    if row['environment'] != 'production':
        return jsonify(error='Compra de prueba: prohibido emitir documentos reales en SII.'), 409
    if row['payment_status'] != 'approved' or row['invoice_status'] != 'pending':
        return jsonify(error='Solo se preparan boletas pendientes de pagos aprobados.'), 409
    return jsonify(
        status='prepared',
        order_id=row['order_id'],
        amount=row['amount'],
        document_type=41,
        message='Solicitud validada internamente. La conexión y emisión SII todavía no están habilitadas.',
        emitted=False,
    )


@billing_bp.post('/admin/boletas/<order>/issued')
@private
def mark_issued(order):
    body = request.get_json(silent=True) or {}
    folio, issued = body.get('folio', ''), body.get('issued_date', '')
    if not isinstance(folio, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', folio):
        return jsonify(error='Ingresa el folio real del SII.'), 400
    try:
        day = datetime.strptime(issued, '%Y-%m-%d').date()
        if day > datetime.now(TZ).date():
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify(error='Fecha de emisión inválida.'), 400
    try:
        with db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM billing_orders WHERE order_id=?', (order,)).fetchone()
            if not row:
                return jsonify(error='Compra no encontrada'), 404
            if row['environment'] != 'production' or row['payment_status'] != 'approved':
                return jsonify(error='Solo se registran boletas de pagos reales aprobados.'), 409
            if day.isoformat() < row['created_at'][:10]:
                return jsonify(error='La emisión no puede ser anterior a la compra.'), 400
            if row['invoice_status'] == 'issued':
                if row['folio'] == folio and row['issued_date'] == issued:
                    return jsonify(ok=True)
                return jsonify(error='Esta compra ya tiene una boleta registrada.'), 409
            con.execute("UPDATE billing_orders SET invoice_status='issued',folio=?,issued_date=?,marked_at=? WHERE order_id=?", (folio,issued,now(),order))
            con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                        (order,'marked_issued',now(),request.authorization.username))
        return jsonify(ok=True)
    except sqlite3.IntegrityError:
        return jsonify(error='Ese folio ya está asignado a otra compra.'), 409


@billing_bp.get('/admin/boletas/export.csv')
@private
def export():
    try:
        rows = filtered_rows()
    except ValueError:
        return jsonify(error='Filtro inválido'), 400
    fields = [('amount','Monto'),('payment_method','Método de pago'),('rut','RUT'),('name','Nombre'),
              ('address','Dirección'),('email','Correo'),('phone','Teléfono'),('detail','Detalle'),
              ('order_id','Orden'),('created_at','Fecha de compra'),('appointment_date','Fecha atención'),
              ('appointment_time','Hora atención'),('environment','Ambiente'),('payment_status','Pago'),
              ('invoice_status','Boleta'),('folio','Folio'),('issued_date','Fecha emisión')]
    def safe(value):
        value = str(value or '')
        return "'" + value if value.lstrip().startswith(('=','+','-','@')) else value
    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow([label for _,label in fields])
    writer.writerows([[safe(row[key]) for key,_ in fields] for row in rows])
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=boletas-psc.csv'})

