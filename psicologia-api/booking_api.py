"""Reservas y Webpay Plus — módulo aislado, DESACTIVADO por defecto.

Registro de rutas solo en rama sandbox. No recibe datos clínicos.
Requiere PostgreSQL persistente, secreto de transacciones manejado por servidor
y una confirmación de disponibilidad obtenida desde Google Calendar.
"""
import os
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, jsonify, request, redirect, Response

from webpay_gateway import WebpayGateway, WebpayError, approved_payment, SESSION_PRICE_CLP

TZ = ZoneInfo("America/Santiago")
PUBLIC_ORIGIN = "https://psicologia-carlos-demo.onrender.com"
booking_bp = Blueprint("bookings", __name__)


class BookingProblem(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def enabled():
    return (os.getenv("PSICOLOGIA_BOOKINGS_ENABLED", "false").lower() == "true"
            and bool(os.getenv("PSICOLOGIA_DATABASE_URL"))
            and os.getenv("PSICOLOGIA_WEBPAY_MODE", "disabled") in ("integration", "production"))


def connection():
    return psycopg.connect(os.environ["PSICOLOGIA_DATABASE_URL"], row_factory=dict_row, connect_timeout=6)


def _calendar_free(d, hhmm):
    """Falla en modo cerrado: si el calendario no puede leerse, no ofrecer cupos."""
    from main import _load_busy_intervals
    intervals = _load_busy_intervals()
    if intervals is None:
        raise BookingProblem("Disponibilidad no verificada. Inténtalo más tarde.", 503)
    st = datetime.combine(d, time.fromisoformat(hhmm), TZ)
    en = st + timedelta(minutes=60)
    return all(not (st < busy_end and busy_start < en) for busy_start, busy_end in intervals)


def _slot(dstr, tstr):
    try:
        d = date.fromisoformat(dstr)
        t = time.fromisoformat(tstr)
    except (ValueError, TypeError):
        raise BookingProblem("Fecha u hora inválida", 400) from None
    mins = t.hour * 60 + t.minute
    if t.tzinfo is not None or t.second or t.microsecond or mins < 9*60 or mins > 19*60 or mins % 15:
        raise BookingProblem("El inicio debe ser entre las 09:00 y las 19:00, cada 15 minutos.", 400)
    dt = datetime.combine(d, t, TZ)
    now = datetime.now(TZ)
    if dt <= now or d > now.date() + timedelta(days=60):
        raise BookingProblem("La hora debe ser futura y dentro de los próximos 60 días.", 400)
    return d, t, dt


def _lock_day(conn, d):
    conn.execute("INSERT INTO psychology_booking_days(slot_date) VALUES (%s) ON CONFLICT DO NOTHING", (d,))
    conn.execute("SELECT slot_date FROM psychology_booking_days WHERE slot_date=%s FOR UPDATE", (d,)).fetchone()


def _expire(conn, d):
    conn.execute(
        """UPDATE psychology_booking_holds SET status='EXPIRED',updated_at=now()
        WHERE slot_date=%s AND status IN ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED')
        AND expires_at <= now()""", (d,))


def _slot_taken(conn, d, start, *, exclude_id=None):
    # El bloqueo por día serializa todos los intentos simultáneos.
    active = conn.execute(
        """SELECT id,starts_at,ends_at FROM psychology_booking_holds
        WHERE slot_date=%s AND (status='CONFIRMED'
          OR (status IN ('HELD','PAYMENT_PENDING','PROCESSING_RETURN','RECONCILIATION_REQUIRED')
          AND expires_at>now()))""", (d,)).fetchall()
    count = sum(row["id"] != exclude_id for row in active)
    slot_end = (datetime.combine(d, start)+timedelta(minutes=60)).time()
    conflict = any(row["id"] != exclude_id and row["starts_at"] < slot_end and start < row["ends_at"]
                   for row in active)
    return count >= 3 or conflict


def _require_ready():
    if not enabled():
        raise BookingProblem("Las reservas pagadas aún no están habilitadas.", 503)
    return WebpayGateway()


def _request_origin():
    # No cookies de usuario; restringir llamadas desde navegadores a nuestro sitio.
    origin = request.headers.get("Origin")
    if origin and origin != PUBLIC_ORIGIN:
        raise BookingProblem("Origen no permitido", 403)


@booking_bp.errorhandler(BookingProblem)
def _booking_error(exc):
    return jsonify(error=str(exc)), exc.status


@booking_bp.get("/api/bookings/config")
def config():
    return jsonify(bookings_enabled=enabled(), webpay_mode=os.getenv("PSICOLOGIA_WEBPAY_MODE","disabled")
                   if enabled() else "disabled", amount_clp=SESSION_PRICE_CLP,
                   daily_limit=3, first_hour="09:00", last_start="19:00", slot_minutes=60,
                   hold_minutes=10)


@booking_bp.post("/api/bookings/hold")
def create_hold():
    _request_origin()
    _require_ready()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BookingProblem("Solicitud inválida",400)
    d,t,_ = _slot(data.get("date"), data.get("time"))
    name = str(data.get("name","")).strip()
    email = str(data.get("email","")).strip().lower()
    if (not 2 <= len(name) <= 120 or "<" in name or ">" in name
        or not 5 <= len(email) <= 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email)):
        raise BookingProblem("Nombre o correo inválido", 400)
    if not _calendar_free(d, t.strftime("%H:%M")):
        raise BookingProblem("Esta hora no está disponible",409)
    hold_id = uuid.uuid4()
    with connection() as conn:
        _lock_day(conn,d)
        _expire(conn,d)
        if _slot_taken(conn,d,t):
            raise BookingProblem("Esta hora ya está ocupada o se alcanzó el máximo diario.",409)
        end = (datetime.combine(d,t)+timedelta(minutes=60)).time()
        conn.execute("""INSERT INTO psychology_booking_holds
          (id,slot_date,starts_at,ends_at,patient_name,patient_email,status,expires_at)
          VALUES (%s,%s,%s,%s,%s,%s,'HELD',now()+interval '10 minutes')""",
          (hold_id,d,t,end,name,email))
    return jsonify(hold_id=str(hold_id),amount_clp=SESSION_PRICE_CLP,
                   expires_in_seconds=600, next_step="checkout"),201


@booking_bp.post("/api/bookings/checkout")
def checkout():
    _request_origin()
    gw=_require_ready()
    data=request.get_json(silent=True)
    if not isinstance(data,dict):
        raise BookingProblem("Solicitud inválida",400)
    try:
        hold_id=uuid.UUID(str(data.get("hold_id","")))
    except ValueError:
        raise BookingProblem("Reserva temporal inválida",400) from None
    with connection() as conn:
        day_row=conn.execute("SELECT slot_date FROM psychology_booking_holds WHERE id=%s",(hold_id,)).fetchone()
        if not day_row:
            raise BookingProblem("La retención no existe",404)
        # Orden único de locks: primero el día, luego la retención.
        _lock_day(conn,day_row["slot_date"])
        hold=conn.execute("SELECT * FROM psychology_booking_holds WHERE id=%s FOR UPDATE",(hold_id,)).fetchone()
        if not hold or hold["status"] != "HELD" or hold["expires_at"]<=datetime.now(TZ):
            raise BookingProblem("La retención ha expirado o ya está en pago",409)
        if not _calendar_free(hold["slot_date"],hold["starts_at"].strftime("%H:%M")):
            raise BookingProblem("La hora pasó a estar ocupada",409)
        _expire(conn,hold["slot_date"])
        if _slot_taken(conn,hold["slot_date"],hold["starts_at"],exclude_id=hold_id):
            raise BookingProblem("Ya no hay cupos disponibles",409)
        attempt_id=uuid.uuid4()
        order="PSI"+secrets.token_hex(8).upper()
        session="PSIS"+secrets.token_hex(8).upper()
        conn.execute("""INSERT INTO psychology_payment_attempts
            (id,hold_id,buy_order,session_id,amount_clp,environment,state)
            VALUES (%s,%s,%s,%s,30000,%s,'CREATING')""",
            (attempt_id,hold_id,order,session,gw.mode))
        conn.execute("""UPDATE psychology_booking_holds SET status='PAYMENT_PENDING',updated_at=now()
            WHERE id=%s""",(hold_id,))
    # Nunca mantener una transacción de base de datos abierta durante petición de red.
    try:
        created=gw.create_for_existing_hold(buy_order=order,session_id=session)
    except WebpayError:
        with connection() as conn:
            conn.execute("""UPDATE psychology_payment_attempts
                SET state='RECONCILIATION_REQUIRED',updated_at=now()
                WHERE id=%s AND state='CREATING'""",(attempt_id,))
            conn.execute("""UPDATE psychology_booking_holds SET status='RECONCILIATION_REQUIRED',
                updated_at=now() WHERE id=%s AND status='PAYMENT_PENDING'""",(hold_id,))
        raise BookingProblem("No se pudo iniciar el pago; contacta a soporte antes de reintentar.",503)
    with connection() as conn:
        conn.execute("""UPDATE psychology_payment_attempts
            SET token_ws=%s,checkout_url=%s,state='READY',updated_at=now()
            WHERE id=%s AND state='CREATING'""",
            (created["token"],created["url"],attempt_id))
    return jsonify(url=created["url"],token_ws=created["token"],amount_clp=SESSION_PRICE_CLP,
                   expires_at="10 minutos desde la selección"),200


def _status_page(label):
    # Respuesta sin identificadores privados; la URL del navegador no determina el estado bancario.
    if label=="confirmed":
        text="Pago autorizado y hora confirmada. Recibirás un correo cuando las notificaciones estén habilitadas."
    elif label=="review":
        text="Se está revisando el resultado del pago. No realices un segundo pago. Contacta al profesional."
    else:
        text="La operación no pudo completarse. No se confirmó ninguna hora."
    html='''<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Estado de pago</title>
    </head><body style="font:16px system-ui;max-width:640px;margin:12vh auto;padding:24px">
    <h1>Resultado de la operación</h1><p>'''+text+'''</p>
    <p><a href="https://psicologia-carlos-demo.onrender.com/#agenda">Volver a la agenda</a></p></body></html>'''
    return Response(html,content_type="text/html; charset=utf-8",
                    headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})


@booking_bp.route("/api/webpay/return",methods=["GET","POST"])
def webpay_return():
    if not enabled():
        return _status_page("failed")
    data=request.values
    token=data.get("token_ws","")
    aborted=data.get("TBK_TOKEN")
    gw=WebpayGateway()
    # Casos de interrupción, incluyendo token_ws + TBK_TOKEN: NUNCA hacer commit.
    if aborted is not None:
        if isinstance(aborted,str) and re.fullmatch(r"[A-Za-z0-9_-]{20,100}",aborted):
            with connection() as conn:
                at=conn.execute("""SELECT * FROM psychology_payment_attempts
                     WHERE token_ws=%s FOR UPDATE""",(aborted,)).fetchone()
                if at and at["state"]=="READY":
                    conn.execute("""UPDATE psychology_payment_attempts SET state='CANCELLED',
                         updated_at=now() WHERE id=%s""",(at["id"],))
                    conn.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',
                         updated_at=now() WHERE id=%s AND status='PAYMENT_PENDING'""",(at["hold_id"],))
        return _status_page("failed")
    if not isinstance(token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}",token):
        return _status_page("review")
    with connection() as conn:
        at=conn.execute("""SELECT * FROM psychology_payment_attempts
            WHERE token_ws=%s FOR UPDATE""",(token,)).fetchone()
        if not at:
            return _status_page("review")
        if at["state"]=="APPROVED":
            return _status_page("confirmed")
        if at["state"] in ("COMMITTING","RECONCILIATION_REQUIRED","PAID_UNALLOCATED"):
            return _status_page("review")
        if at["state"]!="READY" or at["environment"]!=gw.mode:
            return _status_page("failed")
        conn.execute("""UPDATE psychology_payment_attempts SET state='COMMITTING',
            updated_at=now() WHERE id=%s""",(at["id"],))
    # Un solo intento de commit; los timeouts quedan para conciliación, sin reintentos ciegos.
    try:
        response=gw.commit(token)
    except WebpayError:
        with connection() as conn:
            conn.execute("""UPDATE psychology_payment_attempts
            SET state='RECONCILIATION_REQUIRED',updated_at=now()
            WHERE id=%s AND state='COMMITTING'""",(at["id"],))
        return _status_page("review")
    paid=approved_payment(response,buy_order=at["buy_order"],session_id=at["session_id"])
    with connection() as conn:
        day_row=conn.execute("SELECT slot_date FROM psychology_booking_holds WHERE id=%s",
                             (at["hold_id"],)).fetchone()
        if not day_row:
            return _status_page("review")
        _lock_day(conn,day_row["slot_date"])
        hold=conn.execute("SELECT * FROM psychology_booking_holds WHERE id=%s FOR UPDATE",
                          (at["hold_id"],)).fetchone()
        if not hold:
            return _status_page("review")
        _expire(conn,hold["slot_date"])
        if paid:
            # Siempre cotejar cupo antes de asignar una transacción autorizada tardíamente.
            try:
                calendar_available=_calendar_free(hold["slot_date"],hold["starts_at"].strftime("%H:%M"))
            except Exception:
                calendar_available=False
            if (hold["expires_at"]>datetime.now(TZ)
                and not _slot_taken(conn,hold["slot_date"],hold["starts_at"],exclude_id=hold["id"])
                and calendar_available):
                conn.execute("""UPDATE psychology_booking_holds
                    SET status='CONFIRMED',confirmed_at=now(),updated_at=now()
                    WHERE id=%s""",(hold["id"],))
                conn.execute("""UPDATE psychology_payment_attempts
                    SET state='APPROVED',provider_response_code=0,committed_at=now(),updated_at=now()
                    WHERE id=%s""",(at["id"],))
                return _status_page("confirmed")
            conn.execute("""UPDATE psychology_booking_holds
                SET status='PAID_UNALLOCATED',updated_at=now() WHERE id=%s""",(hold["id"],))
            conn.execute("""UPDATE psychology_payment_attempts
                SET state='PAID_UNALLOCATED',provider_response_code=0,committed_at=now(),updated_at=now()
                WHERE id=%s""",(at["id"],))
            return _status_page("review")
        conn.execute("""UPDATE psychology_booking_holds
            SET status='DECLINED',updated_at=now() WHERE id=%s
            AND status!='CONFIRMED'""",(hold["id"],))
        conn.execute("""UPDATE psychology_payment_attempts
            SET state='REJECTED',provider_response_code=%s,updated_at=now()
            WHERE id=%s""",
            (response.get("response_code") if type(response.get("response_code")) is int else None,at["id"]))
    return _status_page("failed")
