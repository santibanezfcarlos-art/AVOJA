"""Webpay Plus REST v1.2. Disabled by default; this release supports integration only.

Protocol/hosts/public sandbox credentials follow TransbankDevelopers/transbank-sdk-python.
Production is deliberately unavailable until a persistent deployment is approved.
"""
import json
import os
import re
import secrets
import time
import threading
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class PaymentError(Exception):
    def __init__(self, message, status=503):
        self.message, self.status = message, status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebpayGateway:
    checkout_host = 'webpay3gint.transbank.cl'
    endpoint = 'https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions'
    # Official public integration credentials, not a merchant's private production keys.
    commerce_code = '597055555532'
    api_key = '579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C'

    def __init__(self, mode=None):
        self.mode = (mode if mode is not None else os.environ.get('VALHALLA_WEBPAY_MODE', 'disabled')).lower()
        if self.mode not in ('disabled', 'integration'):
            raise ValueError('Solo se admite VALHALLA_WEBPAY_MODE=disabled o integration. Los cobros reales no están habilitados.')
        self.public_url = os.environ.get('VALHALLA_PUBLIC_URL', 'https://valhalla-office-demo.onrender.com').rstrip('/')
        url = urlsplit(self.public_url)
        if self.mode == 'integration' and (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('Webpay requiere VALHALLA_PUBLIC_URL con un origen HTTPS válido.')
        self.opener = build_opener(NoRedirect())

    def valid_checkout(self, value):
        if not isinstance(value, str):
            return False
        try:
            url = urlsplit(value)
            return url.scheme == 'https' and url.hostname == self.checkout_host and url.port in (None, 443) and not url.username and not url.password and not url.query and not url.fragment and url.path == '/webpayserver/initTransaction'
        except ValueError:
            return False

    def request(self, method, token=None, body=None):
        if self.mode != 'integration':
            raise PaymentError('Webpay aún no está habilitado.', 409)
        if token is not None and not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', token):
            raise PaymentError('Token de pago inválido.', 400)
        request = Request(self.endpoint + ('/' + token if token else ''), method=method,
                          headers={'Tbk-Api-Key-Id':self.commerce_code, 'Tbk-Api-Key-Secret':self.api_key, 'Content-Type':'application/json'},
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(request, timeout=15) as response:
                data = json.loads(response.read(1_000_001))
                if not isinstance(data, dict):
                    raise ValueError()
                return data
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            # Never expose tokens, headers, or provider responses in errors or app logs.
            raise PaymentError('No se pudo confirmar la respuesta de Transbank. Consulta el estado antes de intentar otro pago.') from None

    def create(self, order, session, amount):
        return self.request('POST', body={'buy_order':order, 'session_id':session, 'amount':amount,
                                          'return_url':self.public_url+'/api/webpay/return'})

    def status(self, token):
        return self.request('GET', token)

    def commit(self, token):
        return self.request('PUT', token, {})


class PaymentService:
    def __init__(self, store, gateway):
        self.store, self.gateway = store, gateway
        self.configuration_lock = threading.RLock()

    def configuration(self):
        with self.store.connection() as c:
            row = c.execute('SELECT * FROM webpay_settings WHERE id=1').fetchone()
        return {'mode':self.mode, 'sandbox_enabled':self.mode=='integration',
                'commerce_code':row['commerce_code'] if row else '',
                'integration_commerce_code':WebpayGateway.commerce_code,
                'production_available':False,
                'return_url':getattr(self.gateway,'public_url',os.environ.get('VALHALLA_PUBLIC_URL','https://valhallaoffice.cl')) + '/api/webpay/return'}

    def restore_configuration(self):
        with self.store.connection() as c:
            row = c.execute('SELECT sandbox_enabled FROM webpay_settings WHERE id=1').fetchone()
        if row:
            self.gateway = WebpayGateway('integration' if row['sandbox_enabled'] else 'disabled')

    def configure(self, actor, body):
        from app import ADMIN_EMAIL, stamp, now
        enabled, code = body.get('sandbox_enabled'), body.get('commerce_code','')
        if type(enabled) is not bool or not isinstance(code,str):
            raise PaymentError('Revisa el código de comercio y la selección de pruebas.',400)
        code=code.strip()
        if code and not re.fullmatch(r'[0-9]{8,12}',code):
            raise PaymentError('Ingresa el código de comercio entregado por Transbank, de 8 a 12 dígitos, sin agregar ceros ni modificarlo.',400)
        if any(key in body for key in ('api_key','secret','production','mode')):
            raise PaymentError('Las credenciales privadas se configuran únicamente en Render. Este panel no activa cobros reales.',400)
        with self.configuration_lock:
            gateway=WebpayGateway('integration' if enabled else 'disabled')
            with self.store.connection(True) as c:
                user=c.execute('SELECT role,email,active FROM users WHERE id=?',(actor,)).fetchone()
                if not user or user['role']!='admin' or user['email']!=ADMIN_EMAIL or not user['active']:
                    raise PaymentError('Acceso exclusivo de administración.',403)
                if gateway.mode!=self.mode and c.execute("SELECT 1 FROM payment_attempts WHERE state NOT IN ('approved','rejected') LIMIT 1").fetchone():
                    raise PaymentError('Primero concilia los pagos pendientes antes de cambiar el ambiente de Webpay.',409)
                c.execute('INSERT INTO webpay_settings VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET commerce_code=excluded.commerce_code,sandbox_enabled=excluded.sandbox_enabled,updated_at=excluded.updated_at',(code,int(enabled),stamp(now())))
                self.store.log(c,actor,'webpay_settings_updated',1)
            self.gateway=gateway
        return self.configuration()

    @property
    def mode(self):
        return self.gateway.mode

    def checkout(self, row):
        if row['state'] != 'ready' or row['environment'] != self.mode:
            raise PaymentError('Hay un pago pendiente de conciliación. Consulta su estado; no inicies otro cobro.', 409)
        return {'payment_id':row['id'], 'token':row['token'], 'url':row['checkout_url'], 'mode':self.mode}

    def create(self, uid, catalog, key):
        with self.configuration_lock:
            return self._create(uid,catalog,key)

    def _create(self, uid, catalog, key):
        if self.mode != 'integration':
            raise PaymentError('Webpay aún no está habilitado.', 409)
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', key):
            raise PaymentError('Recarga la página para iniciar la compra.', 400)
        from app import CATALOG, supports_professional, now, stamp
        if str(catalog) not in CATALOG:
            raise PaymentError('Plan inválido.', 400)
        hours, price = CATALOG[str(catalog)]
        with self.store.connection(True) as c:
            user = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not user or not supports_professional(user) or not user['active'] or not user['email_verified']:
                raise PaymentError('Necesitas una cuenta activa y el correo confirmado para comprar.', 403)
            previous = c.execute('SELECT * FROM payment_attempts WHERE user_id=? AND idempotency_key=?', (uid,key)).fetchone()
            if previous:
                return self.checkout(previous)
            pending = c.execute("SELECT * FROM payment_attempts WHERE user_id=? AND state IN ('creating','ready','uncertain','review')", (uid,)).fetchone()
            if pending:
                if pending['state']=='ready' and pending['hours']==hours:
                    return self.checkout(pending)
                raise PaymentError('Ya tienes un pago pendiente. Retómalo o consulta su estado en Paquetes.', 409)
            pid = c.execute("INSERT INTO packages(user_id,hours,remaining,price,created_at,source) VALUES(?,?,0,?,?,'webpay_integration')", (uid,hours,price,stamp(now()))).lastrowid
            order, session = 'VH'+secrets.token_hex(10), secrets.token_hex(20)
            aid = c.execute("INSERT INTO payment_attempts(user_id,package_id,idempotency_key,buy_order,session_id,amount,hours,environment,state,created_at) VALUES(?,?,?,?,?,?,?,?, 'creating',?)", (uid,pid,key,order,session,price,hours,self.mode,stamp(now()))).lastrowid
            self.store.log(c,uid,'webpay_integration_created',aid)
        try:
            result = self.gateway.create(order,session,price)
            token, url = result.get('token'), result.get('url')
            if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}',token) or not self.gateway.valid_checkout(url):
                raise PaymentError('Transbank no devolvió un inicio de pago válido.')
            with self.store.connection(True) as c:
                c.execute("UPDATE payment_attempts SET token=?,checkout_url=?,state='ready' WHERE id=?", (token,url,aid))
                row = c.execute('SELECT * FROM payment_attempts WHERE id=?',(aid,)).fetchone()
            return self.checkout(row)
        except PaymentError:
            with self.store.connection(True) as c:
                c.execute("UPDATE payment_attempts SET state='uncertain' WHERE id=?", (aid,))
            raise

    def reconcile(self, aid, uid=None, commit=False):
        if self.mode != 'integration':
            raise PaymentError('Webpay aún no está habilitado.', 409)
        with self.store.connection(True) as c:
            row = c.execute('SELECT * FROM payment_attempts WHERE id=?', (aid,)).fetchone()
            if not row or (uid is not None and row['user_id'] != uid):
                raise PaymentError('Pago no encontrado.', 404)
            if row['environment'] != self.mode:
                raise PaymentError('El pago pertenece a otro ambiente.', 409)
            if commit:
                c.execute('UPDATE payment_attempts SET commit_requested=1 WHERE id=?',(aid,))
            commit = commit or bool(row['commit_requested'])
            if row['state'] in ('approved','rejected'):
                return {'state':row['state'], 'payment_id':aid}
            if not row['token']:
                raise PaymentError('La creación del pago quedó sin respuesta. Administración debe revisar esta operación.', 409)
            if row['processing_until'] > time.time():
                return {'state':'processing', 'payment_id':aid}
            # Lease exceeds all HTTP timeouts. Never hold a DB transaction during network calls.
            c.execute('UPDATE payment_attempts SET processing_until=?,checked_at=? WHERE id=?', (time.time()+60,time.time(),aid))
        try:
            result = self.gateway.status(row['token'])
            if result.get('status') == 'INITIALIZED':
                with self.store.connection() as c:
                    commit = commit or bool(c.execute('SELECT commit_requested FROM payment_attempts WHERE id=?',(aid,)).fetchone()[0])
            if result.get('status') == 'INITIALIZED' and commit:
                result = self.gateway.commit(row['token'])
            try:
                amount_matches = not isinstance(result.get('amount'),bool) and Decimal(str(result.get('amount'))) == Decimal(row['amount'])
            except InvalidOperation:
                amount_matches = False
            matches = amount_matches and result.get('buy_order') == row['buy_order'] and result.get('session_id') == row['session_id']
            if not matches:
                with self.store.connection(True) as c:
                    c.execute("UPDATE payment_attempts SET state='review',processing_until=0 WHERE id=?", (aid,))
                    self.store.log(c,None,'webpay_response_mismatch',aid)
                raise PaymentError('La respuesta del pago requiere revisión. No se acreditó saldo.',409)
            code = result.get('response_code')
            approved = result.get('status') == 'AUTHORIZED' and type(code) is int and code == 0
            rejected = result.get('status') in ('FAILED','REVERSED','NULLIFIED')
            state = 'approved' if approved else 'rejected' if rejected else 'ready' if result.get('status') == 'INITIALIZED' else 'uncertain'
            with self.store.connection(True) as c:
                current = c.execute('SELECT state FROM payment_attempts WHERE id=?',(aid,)).fetchone()[0]
                if current == 'approved':
                    return {'state':'approved','payment_id':aid}
                if approved:
                    changed = c.execute("UPDATE packages SET status='paid',remaining=hours WHERE id=? AND status='pending' AND source='webpay_integration'", (row['package_id'],)).rowcount
                    if changed != 1:
                        raise PaymentError('El paquete requiere conciliación administrativa.',409)
                    self.store.log(c,row['user_id'],'webpay_integration_credited',aid)
                elif rejected:
                    c.execute("UPDATE packages SET status='rejected' WHERE id=? AND status='pending'", (row['package_id'],))
                c.execute('UPDATE payment_attempts SET state=?,processing_until=0 WHERE id=?', (state,aid))
            return {'state':state, 'payment_id':aid}
        except PaymentError:
            with self.store.connection(True) as c:
                c.execute("UPDATE payment_attempts SET processing_until=0,state=CASE WHEN state IN ('approved','rejected','review') THEN state ELSE 'uncertain' END WHERE id=?",(aid,))
            raise

    def reconcile_pending(self):
        if self.mode != 'integration':
            return
        with self.store.connection() as c:
            rows = c.execute("SELECT id FROM payment_attempts WHERE token IS NOT NULL AND state IN ('ready','uncertain') AND checked_at<=? AND processing_until<=? ORDER BY checked_at,id LIMIT 5", (time.time()-30,time.time())).fetchall()
        for row in rows:
            try:
                self.reconcile(row['id'])
            except PaymentError:
                pass  # The attempt remains visible and pending; later status checks can recover it.

    def returned(self, fields):
        normal, abort = fields.get('token_ws'), fields.get('TBK_TOKEN')
        if normal and abort:
            raise PaymentError('Retorno de pago inválido.',400)
        token = normal or abort
        with self.store.connection() as c:
            if token:
                row = c.execute('SELECT id FROM payment_attempts WHERE token=?',(token,)).fetchone()
            else:
                row = c.execute('SELECT id FROM payment_attempts WHERE buy_order=? AND session_id=?',(fields.get('TBK_ORDEN_COMPRA'), fields.get('TBK_ID_SESION'))).fetchone()
        if not row:
            raise PaymentError('Retorno de pago no encontrado.',404)
        # Abandonment/timeout fields are untrusted: only a server-to-server status decides the result.
        return self.reconcile(row['id'],commit=bool(normal))
