"""Private administration and versioned public booking settings."""
import copy
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from flask import Blueprint, jsonify, request, send_file
import billing

admin_bp = Blueprint('administration', __name__)
DEFAULT = {
 'weekly': {str(d): ([] if d == 0 else ['10:00','11:00','12:00'] if d == 6 else [f'{h:02}:00' for h in range(9,20)]) for d in range(7)},
 'closedWeekdays': [], 'exceptions': {}, 'maxPerDay': 3,
 'services': {
  'psicoterapia': {'title':'Sesión de Psicoterapia Breve','minutes':45,'price':30000,'active':True,'detail':'Sesión de psicoterapia breve online'},
  'empleabilidad': {'title':'Asesoría en Empleabilidad','minutes':90,'price':45000,'active':True,'detail':'Asesoría en empleabilidad online'},
  'competencias': {'title':'Desarrollo de Competencias','minutes':45,'price':30000,'active':True,'detail':'Desarrollo de competencias online'}
 }}


def settings():
    if not billing.configured():
        return copy.deepcopy(DEFAULT), 0
    with billing.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS admin_settings (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL, version INTEGER NOT NULL)')
        row = con.execute('SELECT value,version FROM admin_settings WHERE id=1').fetchone()
    return (json.loads(row['value']), row['version']) if row else (copy.deepcopy(DEFAULT), 0)


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Configuración inválida.')
    def hours(values):
        if not isinstance(values,list) or len(values)>44:
            raise ValueError('Lista de horas inválida.')
        if any(not isinstance(v,str) or not re.fullmatch(r'(09|1[0-9]):(00|15|30|45)',v) or v>'19:00' for v in values):
            raise ValueError('Usa horas de 09:00 a 19:00, en intervalos de 15 minutos.')
        return sorted(set(values))
    weekly=data.get('weekly')
    if not isinstance(weekly,dict) or set(weekly)!=set(DEFAULT['weekly']):
        raise ValueError('Faltan días del horario semanal.')
    closed=data.get('closedWeekdays',[])
    if not isinstance(closed,list) or any(type(d) is not int or d not in range(7) for d in closed):
        raise ValueError('Días cerrados inválidos.')
    cap=data.get('maxPerDay')
    if type(cap) is not int or not 1<=cap<=3:
        raise ValueError('El máximo debe estar entre 1 y 3 atenciones diarias.')
    exceptions=data.get('exceptions',{})
    if not isinstance(exceptions,dict) or len(exceptions)>400:
        raise ValueError('Demasiadas excepciones de agenda.')
    clean={}
    for key,value in exceptions.items():
        if date.fromisoformat(key).isoformat()!=key or not isinstance(value,dict) or type(value.get('closed')) is not bool:
            raise ValueError('Fecha especial inválida.')
        clean[key]={'closed':value['closed'],'slots':hours(value.get('slots',[]))}
    services=data.get('services')
    if not isinstance(services,dict) or set(services)!=set(DEFAULT['services']):
        raise ValueError('Lista de servicios inválida.')
    clean_services={}
    for key,s in services.items():
        if not isinstance(s,dict) or type(s.get('price')) is not int or not 100<=s['price']<=10000000 or type(s.get('minutes')) is not int or s['minutes'] not in (45,60,90) or type(s.get('active')) is not bool:
            raise ValueError('Revisa precio, duración y estado de los servicios.')
        for field in ('title','detail'):
            if not isinstance(s.get(field),str) or not 3<=len(s[field].strip())<=150 or any(ord(c)<32 for c in s[field]):
                raise ValueError('Nombre o detalle del servicio inválido.')
        clean_services[key]={f:s[f].strip() if isinstance(s[f],str) else s[f] for f in ('title','detail','price','minutes','active')}
    return {'weekly':{d:hours(v) for d,v in weekly.items()},'closedWeekdays':sorted(set(closed)),
            'exceptions':clean,'maxPerDay':cap,'services':clean_services}


def allowed_slot(config, service, day, hour):
    s=config['services'][service]
    if not s['active']:
        return False
    d=date.fromisoformat(day)
    weekday=(d.weekday()+1)%7
    ex=config['exceptions'].get(day)
    slots=([] if ex['closed'] else ex['slots']) if ex else ([] if weekday in config['closedWeekdays'] else config['weekly'][str(weekday)])
    minute=int(hour[:2])*60+int(hour[3:])
    return hour in slots and minute+s['minutes']<=1200


@admin_bp.after_request
def headers(response):
    return billing.private_headers(response)


@admin_bp.get('/api/booking-config')
def public_config():
    try:
        config,version=settings()
        return jsonify(settings=config,version=version)
    except (sqlite3.Error,RuntimeError,OSError):
        return jsonify(error='No se pudo consultar la configuración.'),503


@admin_bp.get('/admin')
@billing.private
def panel():
    return send_file(Path(__file__).parent/'admin.html')


@admin_bp.get('/admin/assets/<asset>')
@billing.private
def assets(asset):
    if asset not in ('admin.js','admin.css'):
        return '',404
    return send_file(Path(__file__).parent/asset)


@admin_bp.route('/admin/settings',methods=['GET','POST'])
@billing.private
def manage_settings():
    if request.method=='GET':
        data,version=settings()
        import webpay_demo_bridge as bridge
        return jsonify(settings=data,version=version,booking_bridge_active=True)
    if (request.content_length or 0)>100000:
        return jsonify(error='Configuración demasiado grande.'),413
    body=request.get_json(silent=True)
    try:
        if not isinstance(body,dict) or type(body.get('version')) is not int:
            raise ValueError('Versión inválida. Recarga el panel.')
        data=validate(body.get('settings'))
        import webpay_demo_bridge as bridge
        if True:  # The reservation service owns prices, durations and capacity.
            if data['maxPerDay'] != 3 or any(data['services'][k][f] != DEFAULT['services'][k][f] for k in DEFAULT['services'] for f in ('price','minutes')):
                raise ValueError('Precio, duración y cupos requieren sincronización con el sistema de reservas.')
    except (ValueError,TypeError,KeyError) as exc:
        return jsonify(error=str(exc)),400
    settings()  # Ensure schema before beginning the write transaction.
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT version FROM admin_settings WHERE id=1').fetchone()
        version=row['version'] if row else 0
        if version!=body['version']:
            return jsonify(error='Otro cambio fue guardado. Recarga antes de editar.'),409
        con.execute('INSERT INTO admin_settings(id,value,version) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,version=excluded.version', (json.dumps(data),version+1))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',('settings','settings_updated',billing.now(),request.authorization.username))
    return jsonify(ok=True,settings=data,version=version+1)


@admin_bp.route('/admin/transbank',methods=['GET','POST'])
@billing.private
def transbank():
    import os
    import webpay_demo as webpay
    if request.method=='POST':
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or type(body.get('paused')) is not bool:
            return jsonify(error='Estado inválido.'),400
        with billing.db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS payment_controls (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)')
            con.execute('INSERT INTO payment_controls(id,paused) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET paused=excluded.paused',(int(body['paused']),))
            con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',('transbank','paused' if body['paused'] else 'resumed',billing.now(),request.authorization.username))
    code=os.environ.get('PSICOLOGIA_WEBPAY_COMMERCE_CODE','')
    return jsonify(mode=os.environ.get('PSICOLOGIA_WEBPAY_MODE','disabled'),commerce_code=code,
                   api_key_configured=bool(os.environ.get('PSICOLOGIA_WEBPAY_API_KEY')),
                   signing_configured=len(os.environ.get(webpay.SIGNING_KEY_VAR,''))>=40,
                   enabled=webpay._enabled(),paused=payments_paused(),production_supported=False,
                   return_url=webpay.RETURN_URL)


def payments_paused():
    if not billing.configured():
        return False
    with billing.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS payment_controls (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)')
        row=con.execute('SELECT paused FROM payment_controls WHERE id=1').fetchone()
    return bool(row and row['paused'])
