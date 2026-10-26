"""Patient intake orchestrates the existing Fonasa operations, without admin access.

Only a validated PDF and explicit activation consent can start a new intake.
Public responses never return historical patient, booking or worker details.
"""
import hashlib
import hmac
import secrets
import time
from flask import Blueprint, jsonify, request, current_app
import billing
import fonasa_internal as f

bp=Blueprint('fonasa_patient',__name__)
ORIGINS=frozenset(('https://pscsantibanez.cl','https://psicologia-carlos-demo.onrender.com'))


@bp.before_request
def protect_patient_intake():
    request.max_content_length=2_050_000
    if request.method=='OPTIONS':return '',204
    if request.headers.get('Origin') not in ORIGINS:
        return jsonify(error='Abre el módulo Fonasa desde la página PSC.'),403
    if not billing.configured():return jsonify(error='El módulo no está disponible temporalmente.'),503
    if (request.content_length or 0)>2_050_000:
        return jsonify(error='Adjunta un PDF de hasta 2 MB.'),413


@bp.after_request
def patient_headers(response):
    origin=request.headers.get('Origin')
    if origin in ORIGINS:
        response.headers['Access-Control-Allow-Origin']=origin
        response.headers['Vary']='Origin'
        response.headers['Access-Control-Allow-Headers']='Content-Type, X-Fonasa-Token'
        response.headers['Access-Control-Allow-Methods']='POST, OPTIONS'
    response.headers['Cache-Control']='no-store, private'
    response.headers['Referrer-Policy']='no-referrer'
    return response


def _result(value):
    response=current_app.make_response(value)
    return response.get_json() or {},response.status_code


def _access(con):
    token=request.headers.get('X-Fonasa-Token','')
    if not 40<=len(token)<=80:return None,None
    row=con.execute('SELECT * FROM fonasa_intake WHERE public_access_hash=?',
                    (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row or int(row['public_access_expiry'] or 0)<time.time():return None,None
    return row,token


def _booking_token(access):
    return hmac.new(access.encode(),b'PSC-Fonasa-booking-v1',hashlib.sha256).hexdigest()


@bp.post('/api/fonasa/intake')
def upload_patient_bono():
    if request.form.get('activation_consent')!='yes':
        return jsonify(error='Confirma que autorizas la validación y activación del bono.'),400
    # Persistent global admission limit also bounds invalid PDFs and remote jobs.
    # No IP addresses or uploaded PDFs are retained.
    with billing.db() as con:
        f._schema(con)
        con.execute('CREATE TABLE IF NOT EXISTS fonasa_public_limits (bucket INTEGER PRIMARY KEY, attempts INTEGER NOT NULL)')
        bucket=int(time.time())//3600
        con.execute('DELETE FROM fonasa_public_limits WHERE bucket<?',(bucket-1,))
        con.execute('INSERT OR IGNORE INTO fonasa_public_limits VALUES(?,0)',(bucket,))
        con.execute('UPDATE fonasa_public_limits SET attempts=attempts+1 WHERE bucket=?',(bucket,))
        attempts=con.execute('SELECT attempts FROM fonasa_public_limits WHERE bucket=?',(bucket,)).fetchone()[0]
    if attempts>20:return jsonify(error='El módulo tiene alta demanda. Intenta más tarde o contacta al profesional.'),429
    data,status=_result(f._upload())
    if status>=400:return jsonify(error=data.get('error','No se pudo leer el bono.')),status
    if data.get('existing'):
        return jsonify(error='Este bono ya está registrado. Continúa en el navegador donde lo cargaste o contacta al profesional para recuperar tu enlace.'),409
    access=secrets.token_urlsafe(32)
    with billing.db() as con:
        con.execute('UPDATE fonasa_intake SET public_access_hash=?,public_access_expiry=? WHERE id=?',
                    (hashlib.sha256(access.encode()).hexdigest(),str(int(time.time())+7*86400),data['bono']['id']))
    return jsonify(access_token=access,state='pdf_read'),201


@bp.post('/api/fonasa/intake/progress')
def advance_patient_bono():
    with billing.db() as con:
        f._schema(con)
        row,access=_access(con)
        if row is None:return jsonify(error='Esta sesión venció. Contacta al profesional para continuar.'),404
        # One concurrent progress request per intake; remote work stays outside SQL locks.
        now=int(time.time())
        con.execute('BEGIN IMMEDIATE')
        claim=con.execute('UPDATE fonasa_intake SET public_polled_at=? WHERE id=? AND (public_polled_at IS NULL OR CAST(public_polled_at AS INTEGER)<?)',
                          (str(now),row['id'],now-15))
        if claim.rowcount!=1:return jsonify(state='processing',retry_after=16),202
    id=row['id']
    state=row['state']
    if state=='pdf_read':
        _,status=_result(f._start_lookup(id))
        if status>=400:return jsonify(state='check_failed',error='No pudimos consultar Fonasa. El bono quedó guardado para revisión.'),503
        return jsonify(state='checking'),202
    if state=='checking':
        result,status=_result(f._lookup_status(id))
        if status>=400:return jsonify(state='checking'),202
        return jsonify(state=result['state']),202
    if state=='check_failed':
        return jsonify(state='check_failed',error='No pudimos validar el bono en Fonasa. Contacta al profesional; tu bono quedó registrado.'),409
    if row['activation_status']=='running':
        result,_=_result(f._bono_activation_status(id))
        return jsonify(state='activating' if result.get('activation_status')=='running' else 'processing'),202
    if row['activation_status']=='needs_review':
        # The existing recovery operation only queries Fonasa; no second activation.
        _result(f._request_bono_activation(id))
        return jsonify(state='reviewing',message='Estamos verificando si Fonasa confirmó la activación.'),202
    if state=='ready_to_activate' and row['activation_status']!='activated':
        _,status=_result(f._request_bono_activation(id))
        return jsonify(state='activating' if status<400 else 'reviewing'),202
    if state in ('ready_to_activate','link_issued') and row['activation_status']=='activated':
        token=_booking_token(access)
        if state=='ready_to_activate':
            result,status=_result(f._issue_booking_link(id,token=token))
            if status>=400:return jsonify(error='El enlace no está disponible. Contacta al profesional.'),409
        elif not hmac.compare_digest(row['token_hash'] or '',hashlib.sha256(token.encode()).hexdigest()):
            return jsonify(error='El profesional actualizó tu enlace. Contáctalo para recuperarlo.'),409
        elif int(row['token_expiry'] or 0)<time.time():
            return jsonify(error='El enlace venció. Contacta al profesional.'),409
        return jsonify(state='ready',link=f.SITE+'/fonasa-reserva.html#'+token,folio=row['folio'])
    if state in ('appointment_requested','calendar_pending','calendar_confirmed'):
        return jsonify(state='already_booked',message='Este bono ya tiene una solicitud de hora. Revisa tu correo o contacta al profesional.')
    return jsonify(state='processing'),202


@bp.post('/api/fonasa/intake/link-received')
def acknowledge_patient_link():
    with billing.db() as con:
        f._schema(con)
        row,access=_access(con)
        if row is None:return jsonify(error='Sesión no válida.'),404
        expected=hashlib.sha256(_booking_token(access).encode()).hexdigest()
        if row['state']!='link_issued' or not hmac.compare_digest(row['token_hash'] or '',expected):
            return jsonify(error='El enlace todavía no está disponible.'),409
        con.execute("UPDATE fonasa_intake SET link_delivered_at=COALESCE(link_delivered_at,?),link_delivery_method=COALESCE(link_delivery_method,'patient_screen') WHERE id=?",(f._utc(),row['id']))
    return jsonify(ok=True)
