"""Real PSC reservation email using the shared branded template and transport."""
import base64
from datetime import datetime, timezone
from icalendar import Calendar, Event, vCalAddress, vText
from demo_notifications import template, send_payload, FROM
from patient_names import display_name


def calendar_attachment(event, start, end):
    """Corporate calendar copy; no personal-Gmail invitation is sent to patients."""
    uid=event.get('iCalUID')
    if not isinstance(uid,str) or not uid:
        uid=event['id']+'@pscsantibanez.cl'
    cal=Calendar()
    cal.add('prodid','-//PSC Santibanez//Reservas//ES')
    cal.add('version','2.0')
    cal.add('method','PUBLISH')
    item=Event()
    item.add('uid',uid)
    item.add('dtstamp',start.astimezone(timezone.utc))
    item.add('dtstart',start)
    item.add('dtend',end)
    item.add('sequence',int(event.get('sequence',0)))
    item.add('summary','Atención online · Ps. Carlos Santibáñez')
    item.add('status','CONFIRMED')
    organizer=vCalAddress('mailto:contacto@pscsantibanez.cl')
    organizer.params['cn']=vText('Ps. Carlos Santibáñez F.')
    item.add('organizer',organizer)
    meet=event.get('hangoutLink')
    if meet and meet.startswith('https://meet.google.com/'):
        item.add('location',meet)
        item.add('description','Sesión online confirmada. Google Meet: '+meet)
    cal.add_component(item)
    return {'filename':'reserva.ics','content':base64.b64encode(cal.to_ical()).decode('ascii'),'content_type':'text/calendar'}


def send(role, source, recipient, patient, start, end, event, *, meet=None, rebooking_url=None, rescheduled=False):
    subject,html=template(role,source,'psicoterapia',start,end,
        patient_name=display_name(patient),meet_url=meet,rebooking_url=rebooking_url,
        demonstration=False,rescheduled=rescheduled)
    text=('Atención online '+('reagendada' if rescheduled else 'confirmada')+'.\n'
          +'Fecha: '+start.strftime('%d/%m/%Y')+'\nHora (Chile): '+start.strftime('%H:%M')
          +'\nDuración: '+str(round((end-start).total_seconds()/60))+' minutos.'
          + ('\nGoogle Meet: '+meet if meet else '')
          + ('\nReagendar mi atención: '+rebooking_url if rebooking_url else '')
          +'\nContacto: contacto@pscsantibanez.cl')
    payload={'from':FROM,'to':[recipient],'subject':subject,'html':html,'text':text}
    if role=='patient':payload['attachments']=[calendar_attachment({**event,**({'hangoutLink':meet} if meet else {})},start,end)]
    return send_payload(payload,'psc-fonasa-'+source+'-'+role+('-rescheduled-v1' if rescheduled else '-confirmed-v1'))
