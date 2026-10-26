"""Production checkout backed by the API's persistent SQLite ledger."""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from flask import Blueprint,Response,jsonify,request,redirect
import billing
from webpay_gateway import Gateway,GatewaySettings,is_authorized,validate_checkout_url
bp=Blueprint('production_checkout',__name__)
TZ=ZoneInfo('America/Santiago')
API='https://psicologia-carlos-calendar-api.onrender.com'
SITE='https://pscsantibanez.cl'
SCHEMA='''CREATE TABLE IF NOT EXISTS production_payments(
 order_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL,
 receipt TEXT UNIQUE NOT NULL, hold_id TEXT NOT NULL, service TEXT NOT NULL,
 starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, expires_at REAL NOT NULL,
 amount INTEGER NOT NULL, state TEXT NOT NULL, token TEXT, event_id TEXT,meet_url TEXT,
 patient_mail INTEGER NOT NULL DEFAULT 0, professional_mail INTEGER NOT NULL DEFAULT 0,
 rebook_token TEXT UNIQUE NOT NULL,rebooked INTEGER NOT NULL DEFAULT 0, new_starts_at TEXT,new_ends_at TEXT);
'''
def init():
    with billing.db() as c:c.executescript(SCHEMA)
def gateway():return Gateway(GatewaySettings.from_environment(mode='production'))
def remote(action,data):
    import webpay_demo_bridge as b
    data=dict(data,channel='production')
    raw=json.dumps(data,separators=(',',':'),ensure_ascii=True,sort_keys=True).encode();stamp=str(int(time.time()))
    sig=hmac.new(os.environ[b.KEY_NAME].encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    req=Request(b.URL+'/internal/psicologia/production/'+action,data=raw,method='POST',headers={'Content-Type':'application/json','X-Psc-Timestamp':stamp,'X-Psc-Signature':sig})
    with urlopen(req,timeout=45) as res:return json.loads(res.read(12000).decode())
def page(title,text,status=200):
    result=Response('<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+' | PSC Santibáñez</title><body style="font:16px/1.6 system-ui;background:#f4f7f3;color:#173e35;padding:5vh 20px"><main style="max-width:650px;margin:auto;background:white;border-radius:20px;padding:28px"><h1>'+html.escape(title)+'</h1>'+text+'<p><a href="'+SITE+'/#agenda">Volver a la página</a></p></main></body></html>',status=status,content_type='text/html; charset=utf-8')
    result.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY'})
    return result

def enabled():
    try:
        code=gateway().config.commerce_code
        with billing.db() as c:
            v=c.execute("SELECT 1 FROM webpay_validation WHERE id=1 AND commerce_code=? AND state='approved'",(code,)).fetchone()
            s=c.execute('SELECT sales_enabled FROM webpay_live_settings WHERE id=1 AND commerce_code=?',(code,)).fetchone()
        return bool(v and (s['sales_enabled'] if s else os.environ.get('PSICOLOGIA_PRODUCTION_SALES_ENABLED')=='true'))
    except Exception:return False

def interval(service,day,hour,duration=None):
    from admin import settings,allowed_slot
    config,_=settings();entry=config['services'][service]
    start=datetime.fromisoformat(day+'T'+hour).replace(tzinfo=TZ);end=start+timedelta(minutes=duration or entry['minutes'])
    if (start<=datetime.now(TZ) or start>datetime.now(TZ)+timedelta(days=60) or not allowed_slot(config,service,day,hour)
        or end.date()!=start.date() or end.hour>20 or (end.hour==20 and end.minute) or start.weekday()==6 or (start.weekday()==5 and (start.hour<9 or end.hour>12 or (end.hour==12 and end.minute)))):raise ValueError('El horario no está disponible.')
    return start,end,entry

def claim(c,start,end,exclude='',busy=()):
    rows=c.execute("SELECT order_id,starts_at,ends_at,new_starts_at,new_ends_at,state FROM production_payments WHERE order_id!=? AND (state IN ('AUTHORIZED','CONFIRMED','FULFILLING','REBOOKING') OR (state IN ('CREATING','READY','COMMITTING','RECONCILIATION') AND expires_at>?))",(exclude,time.time())).fetchall()
    combined={(x.astimezone(TZ).isoformat(),y.astimezone(TZ).isoformat()) for x,y in busy if x.astimezone(TZ).date()==start.date()}
    combined.update((r['starts_at'],r['ends_at']) for r in rows if datetime.fromisoformat(r['starts_at']).date()==start.date())
    if len(combined)>=3:raise ValueError('Se completaron los tres cupos de ese día.')
    if any(r['state']=='REBOOKING' and r['new_starts_at'] and start<datetime.fromisoformat(r['new_ends_at']) and datetime.fromisoformat(r['new_starts_at'])<end for r in rows):raise ValueError('Ese horario tiene una reprogramación pendiente.')
    daily=[r for r in rows if datetime.fromisoformat(r['starts_at']).date()==start.date()]
    if len(daily)>=3 or any(start<datetime.fromisoformat(r['ends_at']) and datetime.fromisoformat(r['starts_at'])<end for r in rows):raise ValueError('El horario acaba de ocuparse. Elige otro.')

@bp.get('/api/webpay/config')
def config():
    try:ready=remote('health',{}).get('state')=='READY'
    except Exception:ready=False
    return jsonify(production=True,sales_enabled=enabled() and ready,fulfillment_ready=ready)

@bp.post('/api/webpay/start')
def start():
    if not enabled():return page('Pagos temporalmente pausados','<p>Las ventas están pausadas. Intenta más tarde.</p>',503)
    if request.headers.get('Origin')!=SITE or (request.content_length or 0)>8000:return page('Solicitud inválida','',403)
    try:
        if request.form.get('booking_policy_accepted')!='1':raise ValueError('Acepta la política de reservas.')
        person=billing.receiver(request.form)
        from patient_names import display_name
        person['name']=display_name(person['name'])
        service=request.form.get('service','');day=request.form.get('date','');hour=request.form.get('time','')
        start_at,end_at,entry=interval(service,day,hour)
        key=request.form.get('payment_request_id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',key):raise ValueError('Actualiza la página antes de continuar.')
        import webpay_demo_bridge as b
        busy=b.busy_intervals(start_at.date(),end_at.date(),include_calendar=True)
        if any(start_at<y and x<end_at for x,y in busy):raise ValueError('El horario está ocupado. Selecciona otro.')
        if remote('health',{}).get('state')!='READY':raise RuntimeError()
        init()
        with billing.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM production_payments WHERE request_id=?',(key,)).fetchone()
            if old:return redirect(API+'/pago/'+old['receipt'],code=303)
            claim(c,start_at,end_at,busy=busy)
            order='PSCP'+secrets.token_hex(9).upper();session='PSCS'+secrets.token_hex(16).upper();receipt=secrets.token_urlsafe(32)
            c.execute('INSERT INTO production_payments(order_id,request_id,session_id,receipt,hold_id,service,starts_at,ends_at,expires_at,amount,state,rebook_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(order,key,session,receipt,str(uuid.uuid4()),service,start_at.isoformat(),end_at.isoformat(),time.time()+1200,entry['price'],'CREATING',secrets.token_urlsafe(32)))
        billing.prepare(order,service,day,hour,entry['price'],person,'production',detail=entry['detail'])
        g=gateway();result=g._request('POST',data={'buy_order':order,'session_id':session,'amount':entry['price'],'return_url':API+'/api/webpay/return'})
        token=result.get('token','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}',token) or not validate_checkout_url(result.get('url'),g.config.host):raise RuntimeError()
        billing.bind_token(order,token)
        with billing.db() as c:c.execute("UPDATE production_payments SET token=?,state='READY' WHERE order_id=?",(token,order))
        return redirect(result['url']+'?'+urlencode({'token_ws':token}),code=303)
    except (ValueError,KeyError) as e:return page('Revisa tu reserva','<p>'+html.escape(str(e))+'</p>',409)
    except Exception:return page('No se pudo iniciar el pago','<p>La operación requiere revisión. No se confirmó una reserva ni debes repetir un pago cuyo resultado desconozcas.</p>',503)

@bp.route('/api/webpay/return',methods=['GET','POST'])
def returning():
    token=request.values.get('token_ws') or request.values.get('TBK_TOKEN','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,100}',token):return page('Operación pendiente','<p>Transbank no entregó un identificador. Consulta el estado antes de reintentar.</p>',409)
    try:
        init()
        with billing.db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM production_payments WHERE token=?',(token,)).fetchone()
            if not r:return page('Operación desconocida','',404)
            previous=r['state']
            if previous=='READY' and request.values.get('TBK_TOKEN'):
                c.execute("UPDATE production_payments SET state='CANCELLED' WHERE order_id=?",(r['order_id'],));return redirect(API+'/pago/'+r['receipt'],code=303)
            if previous=='READY':c.execute("UPDATE production_payments SET state='COMMITTING' WHERE order_id=?",(r['order_id'],))
        if previous in ('READY','COMMITTING','RECONCILIATION'):
            g=gateway()
            try:result=g.commit(token) if previous=='READY' else g.status(token)
            except Exception:result=g.status(token)
            if result.get('buy_order')!=r['order_id'] or result.get('session_id')!=r['session_id'] or result.get('amount')!=r['amount']:raise ValueError()
            if result.get('status')=='INITIALIZED':return redirect(API+'/pago/'+r['receipt'],code=303)
            if result.get('status') not in ('AUTHORIZED','FAILED','REVERSED','NULLIFIED'):raise ValueError()
            paid=is_authorized(result,expected_order=r['order_id'],expected_session=r['session_id'],expected_amount=r['amount'])
            billing.record_result(r['order_id'],token,r['amount'],paid,result.get('payment_type_code',''))
            with billing.db() as c:c.execute('UPDATE production_payments SET state=? WHERE order_id=?',('AUTHORIZED' if paid else 'REJECTED',r['order_id']))
        fulfill(r['order_id'])
        return redirect(API+'/pago/'+r['receipt'],code=303)
    except Exception:
        if 'r' in locals() and r:
            with billing.db() as c:c.execute("UPDATE production_payments SET state='RECONCILIATION',expires_at=? WHERE order_id=? AND state IN ('READY','COMMITTING')",(time.time()+86400,r['order_id']))
        return page('Resultado pendiente de verificación','<p>Estamos verificando el pago. No repitas la compra; consulta a contacto@pscsantibanez.cl.</p>',503)

def fulfill(order):
    with billing.db() as c:
        r=c.execute('SELECT p.*,b.name,b.email FROM production_payments p JOIN billing_orders b USING(order_id) WHERE order_id=?',(order,)).fetchone()
    if not r or r['state'] not in ('AUTHORIZED','CONFIRMED'):return
    data=dict(r)
    if r['state']=='AUTHORIZED':
        with billing.db() as c:
            c.execute('BEGIN IMMEDIATE');claim(c,datetime.fromisoformat(r['starts_at']),datetime.fromisoformat(r['ends_at']),exclude=order)
        result=remote('confirm',{'order':order,'hold_id':r['hold_id'],'service':r['service'],'start':r['starts_at'],'end':r['ends_at'],'name':r['name']})
        if result.get('state')!='CONFIRMED':return
        with billing.db() as c:c.execute("UPDATE production_payments SET state='CONFIRMED',event_id=?,meet_url=? WHERE order_id=?",(result.get('event_id'),result.get('meet_url'),order))
        data.update(state='CONFIRMED',meet_url=result.get('meet_url'))
    for role in ('patient','professional'):
        if data[role+'_mail']:continue
        try:
            result=remote('mail',{'order':order,'service':r['service'],'start':r['starts_at'],'name':r['name'],'email':r['email'],'amount':r['amount'],'role':role,'revision':r['rebooked'],'meet_url':data.get('meet_url'),'rebooking_url':API+'/reagendar-produccion/'+r['rebook_token']})
            if result.get('state')=='SENT':
                with billing.db() as c:c.execute('UPDATE production_payments SET '+role+'_mail=1 WHERE order_id=?',(order,))
        except Exception:pass

@bp.get('/pago/<receipt>')
def receipt_page(receipt):
    init()
    with billing.db() as c:r=c.execute('SELECT * FROM production_payments WHERE receipt=?',(receipt,)).fetchone()
    if not r:return page('Comprobante no encontrado','',404)
    if r['state'] in ('AUTHORIZED','CONFIRMED'):
        try:fulfill(r['order_id'])
        except Exception:pass
        with billing.db() as c:r=c.execute('SELECT * FROM production_payments WHERE receipt=?',(receipt,)).fetchone()
    label={'CONFIRMED':'Tu sesión está confirmada','AUTHORIZED':'Pago aprobado: confirmando tu reserva','CANCELLED':'Pago cancelado','REJECTED':'Pago rechazado'}.get(r['state'],'Operación pendiente de verificación')
    text='<p>'+html.escape(r['service'])+' · '+html.escape(r['starts_at'])+' · $'+str(r['amount'])+' CLP</p>'
    if r['state']=='CONFIRMED':
        text+='<p>Revisa tu correo para ver los detalles de la sesión.</p>'
        if r['meet_url']:text+='<p><a href="'+html.escape(r['meet_url'],quote=True)+'">Entrar a Google Meet</a></p>'
    else:text+='<p><a href="'+API+'/pago/'+r['receipt']+'">Actualizar estado de la reserva</a></p><p>Si el banco aprobó el pago, no repitas la compra. Escríbenos a contacto@pscsantibanez.cl para revisar el estado.</p>'
    return page(label,text)

def busy_intervals(first,last):
    init()
    with billing.db() as c:
        rows=c.execute("SELECT starts_at,ends_at,new_starts_at,new_ends_at FROM production_payments WHERE state IN ('AUTHORIZED','CONFIRMED','FULFILLING','REBOOKING') OR (state IN ('CREATING','READY','COMMITTING','RECONCILIATION') AND expires_at>?)",(time.time(),)).fetchall()
    values=[(datetime.fromisoformat(r['starts_at']),datetime.fromisoformat(r['ends_at'])) for r in rows if first<=datetime.fromisoformat(r['starts_at']).date()<=last]
    values.extend((datetime.fromisoformat(r['new_starts_at']),datetime.fromisoformat(r['new_ends_at'])) for r in rows if r['new_starts_at'])
    from collections import Counter
    for day,count in Counter(x.date() for x,y in values).items():
        if count>=3:
            start=datetime.combine(day,datetime.min.time(),tzinfo=TZ);values.append((start,start+timedelta(days=1)))
    return values

@bp.post('/admin/transbank/live')
@billing.private
def set_sales():
    data=request.get_json(silent=True) or {}
    if type(data.get('enabled')) is not bool:return jsonify(error='Estado inválido'),400
    code=gateway().config.commerce_code
    with billing.db() as c:
        approved=c.execute("SELECT 1 FROM webpay_validation WHERE state='approved' AND commerce_code=?",(code,)).fetchone()
    if not approved:return jsonify(error='Primero completa la validación de $50.'),409
    if data['enabled']:
        try:
            if remote('health',{}).get('state')!='READY':raise ValueError()
        except Exception:return jsonify(error='La conexión de Calendar y correo aún no está lista.'),503
    with billing.db() as c:
        c.execute('INSERT INTO webpay_live_settings(id,commerce_code,sales_enabled,updated_at) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET commerce_code=excluded.commerce_code,sales_enabled=excluded.sales_enabled,updated_at=excluded.updated_at',(code,int(data['enabled']),billing.now()))
    return jsonify(sales_enabled=enabled())

@bp.route('/reagendar-produccion/<token>',methods=['GET','POST'])
def rebook(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,50}',token):return page('Enlace inválido','',404)
    init()
    with billing.db() as c:r=c.execute("SELECT * FROM production_payments WHERE rebook_token=? AND state='CONFIRMED'",(token,)).fetchone()
    if not r:return page('Reserva no encontrada','',404)
    original=datetime.fromisoformat(r['starts_at'])
    if r['rebooked'] or original-datetime.now(TZ)<timedelta(hours=24):return page('Reprogramación no disponible','<p>Se permite un cambio con al menos 24 horas de anticipación. Para solicitudes del mismo día con justificativo médico, escribe a contacto@pscsantibanez.cl.</p>',409)
    if request.method=='GET':return page('Reagendar mi sesión','<p>Selecciona una nueva fecha y hora. El servidor comprobará la disponibilidad antes de confirmar el único cambio permitido.</p><form method="post"><label>Fecha <input required type="date" name="date"></label><label>Hora <input required type="time" name="time" step="900"></label><button>Confirmar reprogramación</button></form>')
    try:
        duration=int((datetime.fromisoformat(r['ends_at'])-original).total_seconds()/60)
        start_at,end_at,_=interval(r['service'],request.form.get('date',''),request.form.get('time',''),duration=duration)
        import webpay_demo_bridge as b
        busy=b.busy_intervals(start_at.date(),start_at.date(),include_calendar=True)
        old_end=datetime.fromisoformat(r['ends_at'])
        busy=[(x,y) for x,y in busy if not (x==original and y==old_end)]
        if any(start_at<y and x<end_at for x,y in busy):raise ValueError('El horario está ocupado.')
        with billing.db() as c:
            c.execute('BEGIN IMMEDIATE');current=c.execute('SELECT * FROM production_payments WHERE order_id=?',(r['order_id'],)).fetchone()
            if current['rebooked'] or current['state']!='CONFIRMED':raise ValueError('Ya hay un cambio registrado o pendiente.')
            claim(c,start_at,end_at,exclude=r['order_id'],busy=busy)
            c.execute("UPDATE production_payments SET state='REBOOKING',new_starts_at=?,new_ends_at=? WHERE order_id=?",(start_at.isoformat(),end_at.isoformat(),r['order_id']))
        result=remote('rebook',{'hold_id':r['hold_id'],'service':r['service'],'old_start':r['starts_at'],'old_end':r['ends_at'],'start':start_at.isoformat(),'end':end_at.isoformat()})
        if result.get('state')!='CONFIRMED':raise RuntimeError()
        with billing.db() as c:
            c.execute("UPDATE production_payments SET state='CONFIRMED',rebooked=1,new_starts_at=NULL,new_ends_at=NULL,starts_at=?,ends_at=?,patient_mail=0,professional_mail=0 WHERE order_id=?",(start_at.isoformat(),end_at.isoformat(),r['order_id']))
            c.execute('UPDATE billing_orders SET appointment_date=?,appointment_time=? WHERE order_id=?',(start_at.date().isoformat(),start_at.strftime('%H:%M'),r['order_id']))
        fulfill(r['order_id'])
        return page('Reprogramación confirmada','<p>Tu reserva quedó actualizada. Revisa tu correo.</p>')
    except ValueError as e:return page('No se pudo cambiar el horario','<p>'+html.escape(str(e))+'</p>',409)
    except Exception:return page('Cambio pendiente de verificación','<p>Conservamos el registro original y estamos verificando Calendar. No hagas otro cambio; escríbenos a contacto@pscsantibanez.cl.</p>',503)

@bp.post('/admin/transbank/reconcile')
@billing.private
def reconcile():
    init();reviewed=0
    with billing.db() as c:rows=c.execute("SELECT * FROM production_payments WHERE state IN ('READY','COMMITTING','RECONCILIATION','AUTHORIZED','CONFIRMED','REBOOKING')").fetchall()
    for r in rows:
        try:
            if r['state']=='REBOOKING':
                result=remote('rebook',{'hold_id':r['hold_id'],'service':r['service'],'old_start':r['starts_at'],'old_end':r['ends_at'],'start':r['new_starts_at'],'end':r['new_ends_at']})
                if result.get('state')!='CONFIRMED':continue
                with billing.db() as c:
                    c.execute("UPDATE production_payments SET state='CONFIRMED',rebooked=1,starts_at=new_starts_at,ends_at=new_ends_at,new_starts_at=NULL,new_ends_at=NULL,patient_mail=0,professional_mail=0 WHERE order_id=?",(r['order_id'],))
                    stamp=datetime.fromisoformat(r['new_starts_at']);c.execute('UPDATE billing_orders SET appointment_date=?,appointment_time=? WHERE order_id=?',(stamp.date().isoformat(),stamp.strftime('%H:%M'),r['order_id']))
            elif r['state'] in ('READY','COMMITTING','RECONCILIATION') and r['token']:
                result=gateway().status(r['token'])
                if result.get('buy_order')!=r['order_id'] or result.get('session_id')!=r['session_id'] or result.get('amount')!=r['amount']:continue
                paid=is_authorized(result,expected_order=r['order_id'],expected_session=r['session_id'],expected_amount=r['amount'])
                if not paid and result.get('status') not in ('FAILED','REVERSED','NULLIFIED'):continue
                billing.record_result(r['order_id'],r['token'],r['amount'],paid,result.get('payment_type_code',''))
                with billing.db() as c:c.execute('UPDATE production_payments SET state=? WHERE order_id=?',('AUTHORIZED' if paid else 'REJECTED',r['order_id']))
            fulfill(r['order_id']);reviewed+=1
        except Exception:continue
    return jsonify(reviewed=reviewed)
