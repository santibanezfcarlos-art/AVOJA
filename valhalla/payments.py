"""Webpay Plus REST: separate integration and explicitly configured production."""
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
        if self.mode not in ('disabled', 'integration', 'production'):
            raise ValueError('VALHALLA_WEBPAY_MODE debe ser disabled, integration o production.')
        self.public_url = os.environ.get('VALHALLA_PUBLIC_URL', 'https://valhalla-office-demo.onrender.com').rstrip('/')
        url = urlsplit(self.public_url)
        if self.mode == 'integration' and (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('Webpay requiere VALHALLA_PUBLIC_URL con un origen HTTPS válido.')
        if self.mode=='production':
            self.commerce_code=os.environ.get('VALHALLA_WEBPAY_COMMERCE_CODE','').strip()
            self.api_key=os.environ.get('VALHALLA_WEBPAY_API_KEY','').strip()
            if not re.fullmatch(r'5970[0-9]{8}',self.commerce_code) or not self.api_key or len(self.api_key)>200 or any(ch.isspace() for ch in self.api_key):
                raise ValueError('Producción requiere VALHALLA_WEBPAY_COMMERCE_CODE de 12 dígitos y VALHALLA_WEBPAY_API_KEY privada en Render.')
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
                raise ValueError('Producción requiere VALHALLA_PUBLIC_URL con un origen HTTPS válido.')
            self.checkout_host='webpay3g.transbank.cl'
            self.endpoint='https://webpay3g.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions'
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
        if self.mode not in ('integration','production'):
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
        live=self.live_configuration()
        return {'live_sales_enabled':live['sales_enabled'],'validation_paid':live['validation_paid'],'mode':self.mode, 'sandbox_enabled':self.mode=='integration',
                'commerce_code':self.gateway.commerce_code if self.mode=='production' else row['commerce_code'] if row else '',
                'integration_commerce_code':WebpayGateway.commerce_code,
                'production_available':self.mode=='production',
                'return_url':getattr(self.gateway,'public_url',os.environ.get('VALHALLA_PUBLIC_URL','https://valhallaoffice.cl')) + '/api/webpay/return'}

    def restore_configuration(self):
        if self.mode=='production':return
        with self.store.connection() as c:
            row = c.execute('SELECT sandbox_enabled FROM webpay_settings WHERE id=1').fetchone()
        if row:
            self.gateway = WebpayGateway('integration' if row['sandbox_enabled'] else 'disabled')

    def configure(self, actor, body):
        from app import ADMIN_EMAIL, stamp, now
        if self.mode=='production':raise PaymentError('El ambiente de producción se configura únicamente en Render.',409)
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

    def live_configuration(self):
        with self.store.connection() as c:
            enabled=c.execute('SELECT sales_enabled FROM webpay_live_settings WHERE id=1 AND commerce_code=?',(self.gateway.commerce_code,)).fetchone()
            paid=c.execute("SELECT 1 FROM payment_attempts WHERE environment='production' AND amount=50 AND hours=0 AND state='approved' AND commerce_code=? LIMIT 1",(self.gateway.commerce_code,)).fetchone()
        return {'sales_enabled':bool(enabled and enabled['sales_enabled']),'validation_paid':bool(paid)}

    def set_live_sales(self,actor,body):
        if self.mode!='production' or type(body.get('enabled')) is not bool or body.get('confirmed') is not True:raise PaymentError('Confirma la activación o pausa de ventas en producción.',400)
        from app import stamp,now
        with self.configuration_lock,self.store.connection(True) as c:
            self.store.require_owner(c,actor)
            if body['enabled'] and not c.execute("SELECT 1 FROM payment_attempts WHERE environment='production' AND amount=50 AND hours=0 AND state='approved' AND commerce_code=? LIMIT 1",(self.gateway.commerce_code,)).fetchone():raise PaymentError('Primero completa la compra de validación de $50 con tarjeta real.',409)
            c.execute('INSERT INTO webpay_live_settings(id,sales_enabled,updated_at,commerce_code) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET sales_enabled=excluded.sales_enabled,updated_at=excluded.updated_at,commerce_code=excluded.commerce_code',(int(body['enabled']),stamp(now()),self.gateway.commerce_code))
            self.store.log(c,actor,'webpay_live_sales_'+str(body['enabled']),1)
        return self.configuration()

    @property
    def mode(self):
        return self.gateway.mode

    def checkout(self, row):
        if row['archived'] or row['state'] != 'ready' or row['environment'] != self.mode:
            raise PaymentError('Hay un pago pendiente de conciliación. Consulta su estado; no inicies otro cobro.', 409)
        return {'payment_id':row['id'], 'token':row['token'], 'url':row['checkout_url'], 'mode':self.mode}

    def create(self, uid, catalog, key, rules_acceptance=None, discount_code='', expected_price=None):
        with self.configuration_lock:
            return self._create(uid,catalog,key,rules_acceptance,discount_code,expected_price)

    def _create(self, uid, catalog, key, rules_acceptance, discount_code, expected_price):
        from rules import validate_acceptance
        if not validate_acceptance(rules_acceptance):
            raise PaymentError('Debes leer y aceptar las normas vigentes antes de comprar.',400)
        if self.mode not in ('integration','production'):
            raise PaymentError('Webpay aún no está habilitado.', 409)
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', key):
            raise PaymentError('Recarga la página para iniciar la compra.', 400)
        from app import CATALOG, supports_professional, now, stamp, ADMIN_EMAIL
        validation=str(catalog)=='validation_50'
        if not validation and str(catalog) not in CATALOG:
            raise PaymentError('Plan inválido.', 400)
        hours, price = (0,50) if validation else CATALOG[str(catalog)]
        with self.store.connection(True) as c:
            user = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not user or not supports_professional(user) or not user['active'] or user['deleted_at'] or self.store.email_blocked(c,user['email']) or not user['email_verified']:
                raise PaymentError('Necesitas una cuenta activa y el correo confirmado para comprar.', 403)
            if validation:
                if self.mode!='production' or user['role']!='admin' or user['email']!=ADMIN_EMAIL or discount_code:raise PaymentError('La validación de $50 es exclusiva del administrador en producción, sin descuentos.',403)
            elif self.mode=='production' and not c.execute('SELECT 1 FROM webpay_live_settings WHERE id=1 AND sales_enabled=1 AND commerce_code=?',(self.gateway.commerce_code,)).fetchone():raise PaymentError('Las compras están temporalmente pausadas. Contacta a administración.',409)
            previous = c.execute('SELECT * FROM payment_attempts WHERE user_id=? AND idempotency_key=?', (uid,key)).fetchone()
            if previous:
                snapshot=c.execute('SELECT discount_code FROM packages WHERE id=?',(previous['package_id'],)).fetchone()
                if snapshot['discount_code']!=str(discount_code).strip().upper() or previous['hours']!=hours or previous['environment']!=self.mode or (expected_price is not None and expected_price!=previous['amount']):raise PaymentError('Esta compra ya fue iniciada con otro plan o código.',409)
                checkout=self.checkout(previous)
                if not c.execute('SELECT 1 FROM purchase_acceptances WHERE package_id=?',(previous['package_id'],)).fetchone():
                    self.store.record_purchase_acceptance(c,uid,previous['package_id'],rules_acceptance)
                return checkout
            # Explicit resume retains the original quote, even after a promotion ends.
            pending = c.execute("SELECT * FROM payment_attempts WHERE user_id=? AND environment=? AND state IN ('creating','ready','uncertain','review') AND archived=0", (uid,self.mode)).fetchone()
            if pending and pending['state']=='ready' and pending['hours']==hours and expected_price==pending['amount']:
                snapshot=c.execute('SELECT discount_code FROM packages WHERE id=?',(pending['package_id'],)).fetchone()
                if snapshot['discount_code']==str(discount_code).strip().upper():
                    return self.checkout(pending)
            quote={'code':'','percent':0,'original_price':50,'price':50} if validation else self.store.discount_quote(c,catalog,discount_code);price=quote['price']
            if expected_price is not None and expected_price!=price:raise PaymentError('El precio cambió. Aplica nuevamente el código antes de comprar.',409)
            if pending:
                if pending['state']=='ready' and pending['hours']==hours and pending['amount']==price and c.execute('SELECT discount_code FROM packages WHERE id=?',(pending['package_id'],)).fetchone()['discount_code']==quote['code']:
                    if not c.execute('SELECT 1 FROM purchase_acceptances WHERE package_id=?',(pending['package_id'],)).fetchone():
                        self.store.record_purchase_acceptance(c,uid,pending['package_id'],rules_acceptance)
                    return self.checkout(pending)
                raise PaymentError('Ya tienes un pago pendiente. Retómalo o consulta su estado en Comprar horas.', 409)
            source='webpay_validation' if validation else 'webpay_production' if self.mode=='production' else 'webpay_integration'
            pid = c.execute("INSERT INTO packages(user_id,hours,remaining,price,created_at,source) VALUES(?,?,0,?,?,?)", (uid,hours,price,stamp(now()),source)).lastrowid
            self.store.record_discount(c,pid,quote)
            self.store.record_purchase_acceptance(c,uid,pid,rules_acceptance)
            order, session = 'VH'+secrets.token_hex(10), secrets.token_hex(20)
            aid = c.execute("INSERT INTO payment_attempts(user_id,package_id,idempotency_key,buy_order,session_id,amount,hours,environment,state,created_at) VALUES(?,?,?,?,?,?,?,?, 'creating',?)", (uid,pid,key,order,session,price,hours,self.mode,stamp(now()))).lastrowid
            c.execute('UPDATE payment_attempts SET commerce_code=? WHERE id=?',(self.gateway.commerce_code,aid))
            self.store.log(c,uid,'webpay_'+self.mode+'_created',aid)
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

    def reconcile(self, aid, uid=None, commit=False, allow_commit=True):
        from app import stamp,now
        if self.mode not in ('integration','production'):
            raise PaymentError('Webpay aún no está habilitado.', 409)
        with self.store.connection(True) as c:
            row = c.execute('SELECT * FROM payment_attempts WHERE id=?', (aid,)).fetchone()
            if not row or (uid is not None and row['user_id'] != uid):
                raise PaymentError('Pago no encontrado.', 404)
            gateway=self.gateway
            if row['environment'] != self.mode:
                if self.mode=='production' and row['environment']=='integration':gateway=WebpayGateway('integration')
                else:raise PaymentError('El pago pertenece a otro ambiente.', 409)
            if row['environment']=='production' and row['commerce_code']!=gateway.commerce_code:raise PaymentError('El pago corresponde a otro código de comercio. Revisa la configuración antes de conciliar.',409)
            if commit and allow_commit:
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
            result = gateway.status(row['token'])
            if result.get('status') == 'INITIALIZED' and allow_commit:
                with self.store.connection() as c:
                    commit = commit or bool(c.execute('SELECT commit_requested FROM payment_attempts WHERE id=?',(aid,)).fetchone()[0])
            if result.get('status') == 'INITIALIZED' and commit and allow_commit:
                result = gateway.commit(row['token'])
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
                    c.execute("UPDATE payment_attempts SET approved_at=? WHERE id=? AND approved_at=''",(stamp(now()),aid))
                    changed = c.execute("UPDATE packages SET status='paid',remaining=hours,archived_at='',archive_reason='',archived_by=NULL WHERE id=? AND status='pending' AND source=?", (row['package_id'],'webpay_validation' if row['environment']=='production' and row['hours']==0 and row['amount']==50 else 'webpay_production' if row['environment']=='production' else 'webpay_integration')).rowcount
                    if changed != 1:
                        raise PaymentError('El paquete requiere conciliación administrativa.',409)
                    self.store.log(c,row['user_id'],'webpay_'+row['environment']+'_credited',aid)
                elif rejected:
                    c.execute("UPDATE packages SET status='rejected' WHERE id=? AND status='pending'", (row['package_id'],))
                c.execute("UPDATE payment_attempts SET state=?,processing_until=0,archived=CASE WHEN ?='approved' THEN 0 ELSE archived END WHERE id=?", (state,state,aid))
            return {'state':state, 'payment_id':aid}
        except PaymentError:
            with self.store.connection(True) as c:
                c.execute("UPDATE payment_attempts SET processing_until=0,state=CASE WHEN state IN ('approved','rejected','review') THEN state ELSE 'uncertain' END WHERE id=?",(aid,))
            raise

    def reconcile_pending(self):
        if self.mode not in ('integration','production'):
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
        token = abort or normal
        with self.store.connection() as c:
            if token:
                row = c.execute('SELECT id,buy_order,session_id FROM payment_attempts WHERE token=?',(token,)).fetchone()
            else:
                row = c.execute('SELECT id,buy_order,session_id FROM payment_attempts WHERE buy_order=? AND session_id=?',(fields.get('TBK_ORDEN_COMPRA'), fields.get('TBK_ID_SESION'))).fetchone()
        if not row:
            raise PaymentError('Retorno de pago no encontrado.',404)
        if any(fields.get(key) and fields[key]!=row[column] for key,column in [('TBK_ORDEN_COMPRA','buy_order'),('TBK_ID_SESION','session_id')]):raise PaymentError('Los datos de retorno no corresponden al pago.',400)
        if not normal or abort:
            with self.store.connection(True) as c:c.execute('UPDATE payment_attempts SET commit_requested=0 WHERE id=?',(row['id'],))
        # Abandonment/timeout fields are untrusted: only a server-to-server status decides the result.
        return self.reconcile(row['id'],commit=bool(normal and not abort),allow_commit=bool(normal and not abort))
