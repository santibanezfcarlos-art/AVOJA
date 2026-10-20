"""Transactional notifications. Jobs are queued with the operation, sent outside its DB lock."""
import json
import time
from html import escape
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from mailer import EmailDeliveryError

CHILE = ZoneInfo('America/Santiago')
ROOMS = {1:'Box 1',2:'Box 2',3:'Box infantil'}


OFFICE_ADDRESS = 'Colo Colo 222, oficina 710'


def booking_links(booking, public_url='https://valhallaoffice.cl'):
    start=datetime.fromisoformat(booking['start']).astimezone(timezone.utc)
    end=start+timedelta(minutes=55)
    reservations=public_url.rstrip('/')+'/#mis-reservas'
    calendar='https://calendar.google.com/calendar/render?'+urlencode({
        'action':'TEMPLATE','text':'Valhalla Office · '+ROOMS[booking['box']],
        'dates':start.strftime('%Y%m%dT%H%M%SZ')+'/'+end.strftime('%Y%m%dT%H%M%SZ'),
        'ctz':'America/Santiago','location':OFFICE_ADDRESS,
        'details':'Reserva personal de 55 minutos. Consulta su estado y modificaciones en '+reservations})
    maps='https://www.google.com/maps/search/?'+urlencode({'api':'1','query':'Valhalla Office, '+OFFICE_ADDRESS})
    return {'calendar':calendar,'directions':maps,'bookings':reservations}


def notification_message(kind, payload, public_url):
    person = payload['name'] + ' (' + payload['email'] + ')'
    if kind == 'admin_signup':
        return ('Nuevo registro profesional · Valhalla Office',
                f"Se inscribió una nueva cuenta profesional.\n\nNombre y correo: {person}\n"
                f"Especialidad: {payload['specialty']}\n\nEste aviso no acredita identidad ni confirma el correo del usuario.\n"
                f"Revisa el estado desde Administración → Usuarios.\n{public_url}/")
    if kind == 'admin_document':
        return ('Documentación recibida · Valhalla Office',
                f"Un profesional envió documentación para revisar.\n\nProfesional: {person}\n"
                f"Envío #{payload['document_id']} · {payload['file_count']} archivo(s)\n"
                f"Tipo: {'Carnet de identidad' if payload['document_kind']=='identity' else 'Certificado de título' if payload['document_kind']=='degree' else 'Acreditación profesional'}\n\n"
                f"Los archivos no se adjuntan al correo. Revísalos únicamente en Administración → Documentos.\n{public_url}/")
    booking = payload['booking']
    start = datetime.fromisoformat(booking['start']).astimezone(CHILE)
    end = start + timedelta(minutes=55)
    titles = {'booking_confirmed':'Reserva confirmada', 'booking_modified':'Reserva modificada', 'booking_cancelled':'Reserva cancelada'}
    title = titles[kind]
    text = (f"{title} · Valhalla Office\n\nProfesional: {payload['name']}\n"
            f"Espacio: {ROOMS[booking['box']]}\n"
            f"Fecha: {start:%d/%m/%Y}\nHorario: {start:%H:%M} a {end:%H:%M}\n"
            "Dirección: Colo Colo 222, oficina 710\n\n")
    if kind == 'booking_cancelled':
        text += 'La reserva fue cancelada por administración y se devolvió una sesión al paquete original.\n'
    else:
        text += ('Ten presente:\n'
                 '• Recibe personalmente a tu consultante.\n'
                 '• Tu reserva es personal e intransferible.\n'
                 '• Puedes modificarla una sola vez, hasta 2 horas antes.\n'
                 '• El nuevo horario requiere al menos 24 horas de anticipación.\n'
                 '• Consulta el estado actualizado en Mis reservas.\n')
    if kind != 'booking_cancelled':
        if 'remaining' in payload:
            text += f"• Saldo disponible al confirmar: {payload['remaining']} sesiones.\n"
        links=booking_links(booking,public_url)
        text += f"\nAgregar a mi calendario: {links['calendar']}\nCómo llegar: {links['directions']}\nMis reservas: {links['bookings']}\n"
    return title + ' · Valhalla Office', text + '\n' + public_url + '/'


def notification_html(kind, payload, public_url, logo_url=None):
    """Table layout and inline styles work without scripts, fonts, or client CSS."""
    if kind not in ('booking_confirmed','booking_modified','booking_cancelled'):
        return None
    booking=payload['booking'];start=datetime.fromisoformat(booking['start']).astimezone(CHILE)
    end=start+timedelta(minutes=55);links=booking_links(booking,public_url)
    cancelled=kind=='booking_cancelled'
    title='Tu reserva fue cancelada' if cancelled else 'Tu reserva fue actualizada' if kind=='booking_modified' else '¡Tu espacio te espera!'
    intro='fue cancelada por administración' if cancelled else 'ha sido actualizada' if kind=='booking_modified' else 'está confirmada'
    weekdays=('lunes','martes','miércoles','jueves','viernes','sábado','domingo')
    months=('enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre')
    date=f'{weekdays[start.weekday()].capitalize()} {start.day} de {months[start.month-1]} de {start.year}'
    if logo_url is None and (Path(__file__).parent/'static/images/valhalla-logo.png').is_file():
        logo_url=public_url.rstrip('/')+'/images/valhalla-logo.png'
    logo=(f'<img src="{escape(logo_url,quote=True)}" width="200" alt="Valhalla" style="display:block;width:200px;max-width:100%;height:auto;border:0;margin:0 auto;">'
          if logo_url else '<span style="font-family:Georgia,serif;font-size:25px;letter-spacing:4px;color:#193e37;">VALHALLA</span>')
    notes=['La reserva fue cancelada y se devolvió una sesión al paquete original.'] if cancelled else [
        'Recibe personalmente a tu consultante.',
        'Tu reserva es personal e intransferible.',
        'Puedes modificarla una sola vez, hasta 2 horas antes.',
        'El nuevo horario requiere al menos 24 horas de anticipación.',
        'Consulta su estado actualizado en Mis reservas.']
    if 'remaining' in payload:
        remaining=payload['remaining'];notes.append(f'Saldo disponible al confirmar: {remaining} '+('sesión.' if remaining==1 else 'sesiones.'))
    if kind=='booking_modified':notes.append('Si guardaste el horario anterior en tu calendario, actualízalo también.')
    bullets=''.join(f'<li style="margin:0 0 12px;padding-left:4px;">{escape(note)}</li>' for note in notes)
    actions=[('Mis reservas',links['bookings'])]
    if not cancelled:actions += [('Agregar al calendario',links['calendar']),('Cómo llegar',links['directions'])]
    buttons=''.join(f'<tr><td align="center" style="padding:0 0 12px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"><tr><td align="center" bgcolor="{"#245e50" if index==0 else "#f1f5ec"}" style="border:1px solid {"#245e50" if index==0 else "#c8d8cc"};border-radius:9px;"><a href="{escape(url,quote=True)}" target="_blank" style="display:block;padding:16px 12px;font-family:Arial,sans-serif;font-size:16px;line-height:22px;font-weight:bold;text-decoration:none;color:{"#ffffff" if index==0 else "#193e37"};">{label}</a></td></tr></table></td></tr>' for index,(label,url) in enumerate(actions))
    return f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · Valhalla Office</title></head>
<body style="margin:0;padding:0;background-color:#f4f6ef;font-family:Arial,sans-serif;color:#193e37;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(ROOMS[booking['box']])} · {escape(date)} · {start:%H:%M}–{end:%H:%M} · Valhalla Office</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f6ef"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:580px;background-color:#ffffff;border:1px solid #dce4d7;border-radius:16px;">
<tr><td align="center" style="padding:28px 24px 12px;">{logo}<p style="margin:10px 0 0;font-size:11px;letter-spacing:3px;color:#66786e;">OFFICE &amp; WELLBEING</p></td></tr>
<tr><td style="padding:20px 26px 8px;"><h1 style="margin:0 0 20px;font-size:27px;line-height:35px;color:#193e37;">{escape(title)}</h1><p style="margin:0;font-size:16px;line-height:26px;">Hola, {escape(payload['name'])}.<br>Tu reserva en <strong>{escape(ROOMS[booking['box']])}</strong> {intro}.</p></td></tr>
<tr><td style="padding:20px 26px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f1f5ec" style="border-radius:10px;"><tr><td style="padding:20px;"><p style="margin:0 0 8px;font-size:17px;line-height:26px;font-weight:bold;">{escape(date)}</p><p style="margin:0 0 8px;font-size:24px;font-weight:bold;line-height:32px;">{start:%H:%M}–{end:%H:%M}</p><p style="margin:0 0 14px;font-size:12px;color:#66786e;">Horario de Chile · Atención de 55 minutos</p><p style="margin:0;font-size:15px;line-height:24px;">{escape(OFFICE_ADDRESS)}</p></td></tr></table></td></tr>
<tr><td style="padding:4px 26px 16px;"><h2 style="margin:0 0 16px;font-size:18px;">Ten presente</h2><ul style="margin:0;padding-left:21px;font-size:15px;line-height:24px;color:#425b50;">{bullets}</ul></td></tr>
<tr><td style="padding:8px 26px 20px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{buttons}</table></td></tr>
<tr><td align="center" style="padding:0 26px 26px;"><p style="margin:0;font-size:12px;line-height:20px;color:#66786e;">Valhalla Office · Un espacio para tu práctica profesional.</p></td></tr>
</table></td></tr></table></body></html>'''


class NotificationDispatcher:
    def __init__(self, store, delivery):
        self.store, self.delivery = store, delivery

    def claim(self):
        with self.store.connection(True) as c:
            timestamp = time.time()
            # Stop uncertain deliveries before the provider's 24-hour idempotency window expires.
            c.execute("UPDATE notification_outbox SET status='failed' WHERE status IN ('pending','retry','processing') AND first_attempt>0 AND first_attempt<=?",(timestamp-23*3600,))
            c.execute("UPDATE notification_outbox SET status='failed' WHERE status='processing' AND lease_until<=? AND attempts>=5", (timestamp,))
            job = c.execute("""SELECT * FROM notification_outbox WHERE attempts<5 AND
                ((status IN ('pending','retry') AND next_attempt<=?) OR (status='processing' AND lease_until<=?))
                ORDER BY id LIMIT 1""", (timestamp,timestamp)).fetchone()
            if not job:
                return None
            c.execute("UPDATE notification_outbox SET status='processing',lease_until=?,attempts=attempts+1,first_attempt=CASE WHEN first_attempt=0 THEN ? ELSE first_attempt END WHERE id=?", (timestamp+60,timestamp,job['id']))
            job = dict(job);job['attempts'] += 1
            return job

    def dispatch_pending(self, limit=1):
        if not self.delivery or not self.delivery.configured:
            return 0
        processed = 0
        for _ in range(limit):
            job = self.claim()
            if not job:
                break
            provider = getattr(self.delivery,'provider','test')
            eid = self.store.mail_event(job['user_id'],job['kind'],provider,'pending')
            category, code = '', None
            try:
                self.delivery.send_notification(job['recipient'],job['kind'],json.loads(job['payload']),job['delivery_key'])
                status = 'accepted'
            except RuntimeError as error:
                status = 'retry' if job['attempts']<5 else 'failed'
                category = error.category if isinstance(error,EmailDeliveryError) else 'delivery_error'
                code = error.http_status if isinstance(error,EmailDeliveryError) else None
            self.store.mail_outcome(eid,'accepted' if status=='accepted' else 'error',category,code)
            with self.store.connection(True) as c:
                delay = (15,60,180,600,1800)[job['attempts']-1]
                # A modified/cancelled reservation supersedes older pending receipts, including leases.
                c.execute("UPDATE notification_outbox SET status=?,lease_until=0,next_attempt=? WHERE id=? AND status='processing' AND attempts=?",
                          (status,time.time()+delay,job['id'],job['attempts']))
            processed += 1
        return processed
