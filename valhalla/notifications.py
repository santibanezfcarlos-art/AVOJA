"""Transactional notifications. Jobs are queued with the operation, sent outside its DB lock."""
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from mailer import EmailDeliveryError

CHILE = ZoneInfo('America/Santiago')
ROOMS = {1:'Box 1',2:'Box 2',3:'Box infantil'}


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
        text += ('La reserva pertenece exclusivamente a tu cuenta. '
                 'Consulta el estado actualizado en Mis reservas; se permite una sola modificación, con al menos dos horas de anticipación.\n')
    return title + ' · Valhalla Office', text + '\n' + public_url + '/'


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
