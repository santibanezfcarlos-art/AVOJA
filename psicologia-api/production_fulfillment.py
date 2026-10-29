"""Signed stateless Google/Resend adapter; production ledger stays on PSC's persistent disk."""
import os
import re
from datetime import datetime
from html import escape
from flask import Blueprint, jsonify
from public_demo_bridge import _authorize
from google_calendar_writer import CalendarWriter
from demo_notifications import send_payload, template, FROM
bp=Blueprint('production_fulfillment',__name__)
TITLES={'psicoterapia':'Psicoterapia breve','empleabilidad':'Asesoría en empleabilidad','competencias':'Potencia tus competencias'}

def authorized(*, max_content_length=1800):
    if os.environ.get('PSICOLOGIA_PRODUCTION_FULFILLMENT_ENABLED')!='true':
        return None
    data=_authorize(max_content_length=max_content_length)
    return data if data and data.get('channel')=='production' else None

@bp.post('/internal/psicologia/production/check')
def check():
    d=authorized()
    if not d:return jsonify(state='NOT_AUTHORIZED'),403
    try:
        free=CalendarWriter().is_free(datetime.fromisoformat(d['start']),datetime.fromisoformat(d['end']))
        return jsonify(state='FREE' if free else 'BUSY')
    except Exception:return jsonify(state='UNAVAILABLE'),503

@bp.post('/internal/psicologia/production/confirm')
def confirm():
    d=authorized()
    if not d:return jsonify(state='NOT_AUTHORIZED'),403
    try:
        if not re.fullmatch(r'PSCP[A-F0-9]{18}',d['order']) or d['service'] not in TITLES:
            raise ValueError()
        writer=CalendarWriter();start=datetime.fromisoformat(d['start']);end=datetime.fromisoformat(d['end'])
        args=dict(hold_id=d['hold_id'],service=d['service'],start=start,end=end)
        if not writer.own_event_exists(**args) and not writer.is_free(start,end):
            return jsonify(state='BUSY'),409
        event=writer.create_paid_appointment(**args,payment_verified=True,demo_only=False,patient_name=d['name'])
        writer.transport('PATCH',writer._calendar_path+'/'+event,{'sendUpdates':'none'},{'summary':TITLES[d['service']]+' – '+d['name']})
        meet=writer.get_meet_link(d['hold_id'])
        if os.environ.get('PSICOLOGIA_GOOGLE_MEET_ENABLED')=='true' and not meet:return jsonify(state='PENDING'),503
        return jsonify(state='CONFIRMED',event_id=event,meet_url=meet)
    except Exception:return jsonify(state='PENDING'),503

@bp.post('/internal/psicologia/production/mail')
def mail():
    d=authorized()
    if not d:return jsonify(state='NOT_AUTHORIZED'),403
    try:
        if not re.fullmatch(r'PSCP[A-F0-9]{18}',d['order']) or d['service'] not in TITLES or d['role'] not in ('patient','professional'):
            raise ValueError()
        recipient=d['email'] if d['role']=='patient' else os.environ['PSICOLOGIA_PILOT_PRO_EMAIL']
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',recipient):raise ValueError()
        from zoneinfo import ZoneInfo
        stamp=datetime.fromisoformat(d['start']).astimezone(ZoneInfo('America/Santiago')).strftime('%d/%m/%Y a las %H:%M')
        title=('Tu hora está confirmada' if d['role']=='patient' else 'Nueva sesión confirmada')
        text=f"{title}\n{TITLES[d['service']]}\n{stamp} (Chile)\nPaciente: {d['name']}\nPago Webpay confirmado: ${format(d['amount'], ',').replace(',', '.')} CLP\n"
        if d.get('meet_url'):text+='Google Meet: '+d['meet_url']+'\n'
        text+='Reagendar: '+d['rebooking_url']+'\nUna reprogramación con al menos 24 horas de anticipación.\nContacto: contacto@pscsantibanez.cl'
        subject,body=template(d['role'],d['order'],d['service'],
            datetime.fromisoformat(d['start']),
            datetime.fromisoformat(d['end']) if d.get('end') else None,
            patient_name=d['name'],meet_url=d.get('meet_url'),
            rebooking_url=d['rebooking_url'] if d['role']=='patient' else None,
            demonstration=False,webpay_amount=d['amount'],
            rescheduled=bool(d.get('revision',0)))
        send_payload({'from':FROM,'to':[recipient],'subject':subject,'html':body,'text':text},'psc-production-'+d['order']+'-'+d['role']+'-'+str(d.get('revision',0)))
        return jsonify(state='SENT')
    except Exception:return jsonify(state='PENDING'),503


@bp.post('/internal/psicologia/production/receipt-mail')
def receipt_mail():
    """Signed API-only transmission of an ALREADY issued official PDF.

    Distinct from issuing the tax document. Never accepts unauthenticated
    recipients, attachments or idempotency keys from web browsers.
    """
    d=authorized(max_content_length=7_100_000)
    if not d:return jsonify(state='NOT_AUTHORIZED'),403
    try:
        import base64
        import hashlib
        from demo_notifications import send_payload
        order,folio=d.get('order'),d.get('folio')
        data=d.get('mail')
        if (not isinstance(order,str) or not re.fullmatch(r'PSCP[0-9A-F]{18}',order)
            or not isinstance(folio,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',folio)
            or not isinstance(data,dict) or set(data) != {'from','to','subject','html','text','attachments'}):
            raise ValueError()
        if data['from']!='Ps. Carlos Santibáñez F. <contacto@pscsantibanez.cl>':
            raise ValueError()
        if (not isinstance(data['to'],list) or len(data['to'])!=1
            or not isinstance(data['to'][0],str)
            or not re.fullmatch(r'[^\\s@]+@[^\\s@]+\\.[^\\s@]+',data['to'][0])):
            raise ValueError()
        attachment=data['attachments']
        if not isinstance(attachment,list) or len(attachment)!=1 or not isinstance(attachment[0],dict):
            raise ValueError()
        obj=attachment[0]
        if (obj.get('filename')!='boleta-'+folio+'.pdf' or obj.get('content_type')!='application/pdf'
            or not isinstance(obj.get('content'),str)):
            raise ValueError()
        pdf=base64.b64decode(obj['content'],validate=True)
        if (not 100<=len(pdf)<=5_000_000 or not pdf.startswith(b'%PDF-')
            or b'%%EOF' not in pdf[-2048:]):
            raise ValueError()
        if not all(isinstance(data.get(k),str) and 1<=len(data[k])<=50000 for k in ('subject','html','text')):
            raise ValueError()
        provider_id=send_payload(data,'psc-receipt-'+hashlib.sha256((order+':'+folio+':psc').encode()).hexdigest())
        return jsonify(state='SENT',reference=provider_id)
    except ValueError:
        return jsonify(state='INVALID'),400
    except Exception:
        return jsonify(state='UNCERTAIN'),503

@bp.post('/internal/psicologia/production/health')
def health():
    if not authorized():return jsonify(state='NOT_AUTHORIZED'),403
    try:
        w=CalendarWriter()
        ready=w.settings.configured and w.settings.write_enabled and bool(os.environ.get('PSICOLOGIA_PILOT_RESEND_API_KEY'))
        return jsonify(state='READY' if ready else 'UNAVAILABLE')
    except Exception:return jsonify(state='UNAVAILABLE'),503

@bp.post('/internal/psicologia/production/rebook')
def rebook():
    d=authorized()
    if not d:return jsonify(state='NOT_AUTHORIZED'),403
    try:
        w=CalendarWriter();start=datetime.fromisoformat(d['start']);end=datetime.fromisoformat(d['end'])
        if w.own_event_exists(hold_id=d['hold_id'],service=d['service'],start=start,end=end):return jsonify(state='CONFIRMED')
        if not w.is_free(start,end):return jsonify(state='BUSY'),409
        w.reschedule_own_appointment(hold_id=d['hold_id'],service=d['service'],old_start=datetime.fromisoformat(d['old_start']),old_end=datetime.fromisoformat(d['old_end']),new_start=start,new_end=end)
        return jsonify(state='CONFIRMED')
    except Exception:return jsonify(state='PENDING'),503

