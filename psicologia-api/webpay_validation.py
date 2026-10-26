"""Owner-only, fixed-price Webpay production validation purchase."""
import hashlib
import re
import secrets
import sqlite3
from flask import Blueprint, Response, jsonify, request
import billing
from webpay_gateway import Gateway, GatewayError, GatewaySettings, validate_checkout_url

bp = Blueprint('webpay_validation', __name__)
SITE = 'https://pscsantibanez.cl'

def _page(title, message, status=200):
    from html import escape
    return Response('<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title><body style="font:16px system-ui;background:#f4f7f3;color:#173e35;padding:8vh 20px"><main style="max-width:620px;margin:auto;background:white;border-radius:18px;padding:28px"><h1>'+escape(title)+'</h1><p>'+escape(message)+'</p><a href="'+SITE+'/admin#transbank">Volver a administración</a></main></body></html>', status=status, content_type='text/html; charset=utf-8')

@bp.post('/admin/transbank/validation')
@billing.private
def start_validation():
    try:
        gateway = Gateway(GatewaySettings.from_environment(mode='production'))
        with billing.db() as con:
            current=con.execute('SELECT state FROM webpay_validation WHERE id=1').fetchone()
        if current and current['state']=='pending':
            return jsonify(error='Ya hay una validación pendiente. Revisa primero su resultado en Transbank.'),409
        order = 'PSIV' + secrets.token_hex(9).upper()
        session = 'PSIVS' + secrets.token_hex(12).upper()
        result = gateway._request('POST', data={'buy_order': order, 'session_id': session,
            'amount': 50, 'return_url': gateway.config.return_url.replace('/api/webpay/return','/api/webpay/validation/return')})
        if not isinstance(result.get('token'), str) or not validate_checkout_url(result.get('url'), gateway.config.host):
            raise GatewayError('Transbank devolvió una respuesta inválida.')
        with billing.db() as con:
            con.execute('INSERT INTO webpay_validation(commerce_code,order_id,session_id,token_hash,state,amount,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET commerce_code=excluded.commerce_code,order_id=excluded.order_id,session_id=excluded.session_id,token_hash=excluded.token_hash,state=excluded.state,amount=excluded.amount,created_at=excluded.created_at,paid_at=NULL',
                (gateway.config.commerce_code, order, session, hashlib.sha256(result['token'].encode()).hexdigest(), 'pending', 50, billing.now()))
        return jsonify(url=result['url']+'?token_ws='+result['token'])
    except (GatewayError, RuntimeError, sqlite3.Error, OSError):
        return jsonify(error='No se pudo iniciar la validación. Revisa que producción esté configurada en el servidor.'),503

@bp.route('/api/webpay/validation/return', methods=['GET','POST'])
def validation_return():
    token = request.values.get('token_ws','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', token):
        return _page('Validación no completada', 'Transbank no entregó un identificador válido.', 400)
    try:
        with billing.db() as con:
            row=con.execute('SELECT * FROM webpay_validation WHERE id=1').fetchone()
        if not row or row['token_hash']!=hashlib.sha256(token.encode()).hexdigest() or row['state']!='pending':
            return _page('Validación pendiente', 'No se encontró una compra pendiente para confirmar. Revisa el panel antes de iniciar otra.', 409)
        result=Gateway(GatewaySettings.from_environment(mode='production')).commit(token)
        approved=(result.get('status')=='AUTHORIZED' and result.get('response_code')==0
                  and result.get('buy_order')==row['order_id'] and result.get('session_id')==row['session_id']
                  and result.get('amount')==50)
        with billing.db() as con:
            con.execute('UPDATE webpay_validation SET state=?,paid_at=? WHERE id=1 AND state=? AND order_id=?',
                ('approved' if approved else 'rejected',billing.now() if approved else None,'pending',row['order_id']))
        return _page('Compra de validación aprobada' if approved else 'Compra de validación no aprobada',
            'Transbank confirmó el pago de $50. La compra no genera una reserva ni acredita una sesión.' if approved else 'El pago no quedó autorizado. Revisa el estado en administración antes de reintentar.')
    except (GatewayError, RuntimeError, sqlite3.Error, OSError):
        return _page('Resultado pendiente de conciliación', 'No se pudo confirmar con Transbank. Revisa el panel antes de iniciar otro pago.', 503)
