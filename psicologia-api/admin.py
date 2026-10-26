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
        if data['maxPerDay'] != 3:
            raise ValueError('El máximo permitido sigue siendo tres atenciones diarias.')
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



COMMUNICATION_DEFAULTS = {
 'patient_heading':'¡Tu hora está reservada!',
 'patient_message':'¡Felicitaciones por dar este paso! Tu horario quedó reservado y estos son los detalles.',
 'patient_notes':'La atención será online. Te recomendamos un espacio privado y tranquilo, con conexión estable.',
 'professional_heading':'Tienes una nueva sesión agendada',
 'professional_message':'Se ha agendado una nueva atención en tu calendario.',
 'professional_notes':'Puedes revisar la sesión en tu agenda de Google Calendar.',
 'signature':'Ps. Carlos Santibáñez Fica'
}

def communication_config():
    if not billing.configured():
        return dict(COMMUNICATION_DEFAULTS), 0
    with billing.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS admin_communications (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL, version INTEGER NOT NULL)')
        row=con.execute('SELECT value,version FROM admin_communications WHERE id=1').fetchone()
    return (json.loads(row['value']),row['version']) if row else (dict(COMMUNICATION_DEFAULTS),0)

def validate_communications(payload):
    if not isinstance(payload,dict) or set(payload)!=set(COMMUNICATION_DEFAULTS):
        raise ValueError('Los textos de correo están incompletos.')
    result={}
    for key,value in payload.items():
        if not isinstance(value,str) or not 3<=len(value.strip())<=700 or any(ord(c)<32 and c not in (chr(10),chr(9)) for c in value):
            raise ValueError('Revisa el texto de '+key+'.')
        result[key]=value.strip()
    return result

@admin_bp.route('/admin/communications',methods=['GET','POST'])
@billing.private
def admin_communications():
    if request.method=='GET':
        values,version=communication_config()
        return jsonify(communications=values,version=version)
    if (request.content_length or 0)>10000:
        return jsonify(error='Contenido demasiado extenso.'),413
    body=request.get_json(silent=True)
    try:
        if not isinstance(body,dict) or type(body.get('version')) is not int:
            raise ValueError('Versión de edición inválida.')
        values=validate_communications(body.get('communications'))
    except (ValueError,TypeError) as exc:
        return jsonify(error=str(exc)),400
    communication_config()
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT version FROM admin_communications WHERE id=1').fetchone()
        current=row['version'] if row else 0
        if current!=body['version']:
            return jsonify(error='Otra edición fue guardada. Actualiza el panel.'),409
        con.execute('INSERT INTO admin_communications(id,value,version) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value,version=excluded.version',
                    (json.dumps(values,ensure_ascii=False),current+1))
    return jsonify(communications=values,version=current+1,ok=True)

@admin_bp.get('/api/communications-public')
def public_communications():
    try:
        values,version=communication_config()
        return jsonify(communications=values,version=version)
    except (RuntimeError,sqlite3.Error,OSError):
        return jsonify(error='Configuración temporalmente no disponible.'),503

@admin_bp.route('/admin/transbank',methods=['GET','POST'])
@billing.private
def transbank():
    import os
    import webpay_demo as webpay
    from webpay_gateway import Gateway, GatewayError, GatewaySettings, validate_checkout_url
    if request.method=='POST':
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or type(body.get('paused')) is not bool:
            return jsonify(error='Estado inválido.'),400
        with billing.db() as con:
            con.execute('CREATE TABLE IF NOT EXISTS payment_controls (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)')
            con.execute('INSERT INTO payment_controls(id,paused) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET paused=excluded.paused',(int(body['paused']),))
            con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',('transbank','paused' if body['paused'] else 'resumed',billing.now(),request.authorization.username))
    production_ready=False
    production_code=''
    validation_paid=False
    try:
        gateway=Gateway(GatewaySettings.from_environment(mode='production'))
        production_ready=True
        production_code=gateway.config.commerce_code
        with billing.db() as con:
            row=con.execute('SELECT state,commerce_code FROM webpay_validation WHERE id=1').fetchone()
            validation_paid=bool(row and row['state']=='approved' and row['commerce_code']==production_code)
    except (GatewayError,RuntimeError,sqlite3.Error,OSError):
        pass
    code=os.environ.get('PSICOLOGIA_WEBPAY_COMMERCE_CODE','')
    return jsonify(mode=os.environ.get('PSICOLOGIA_WEBPAY_MODE','disabled'),commerce_code=code,
                   api_key_configured=bool(os.environ.get('PSICOLOGIA_WEBPAY_API_KEY')),
                   signing_configured=len(os.environ.get(webpay.SIGNING_KEY_VAR,''))>=40,
                   enabled=webpay._enabled(),paused=payments_paused(),production_supported=True,
                   production_ready=production_ready,production_commerce_code=production_code,
                   validation_paid=validation_paid,live_sales_enabled=False,
                   production_return_url=os.environ.get('PSICOLOGIA_API_PUBLIC_URL','https://psicologia-carlos-calendar-api.onrender.com')+'/api/webpay/validation/return',
                   return_url=webpay.RETURN_URL)


def payments_paused():
    if not billing.configured():
        return False
    with billing.db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS payment_controls (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL)')
        row=con.execute('SELECT paused FROM payment_controls WHERE id=1').fetchone()
    return bool(row and row['paused'])
