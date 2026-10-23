"""Retención de horas en PostgreSQL, aislada y desactivada por defecto.

Piloto para pruebas de concurrencia; NO crea eventos Google ni cobra dinero.
No recopila datos personales. No conectar a pacientes mientras no exista
persistencia, conciliación financiera, antiabuso y Calendar con escritura.
"""
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import hmac
import os
import re
import secrets
import uuid
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

TZ = ZoneInfo("America/Santiago")
CATALOG = {
    "psicoterapia": {"minutes": 45, "amount_clp": 30000},
    "empleabilidad": {"minutes": 90, "amount_clp": 45000},
    "competencias": {"minutes": 45, "amount_clp": 30000},
}
MAX_DAILY = 3
HOLD_SECONDS = 600
MAX_DAYS_AHEAD = 60
bp = Blueprint("psychology_holds_pilot", __name__)


class HoldProblem(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


def enabled():
    return (os.environ.get("PSICOLOGIA_HOLDS_ENABLED", "false").lower() == "true"
            and bool(os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL"))
            and len(os.environ.get("PSICOLOGIA_HOLDS_PILOT_KEY", "")) >= 32)


def _authorize():
    # El piloto NO tiene controles de acceso para usuarios: únicamente una
    # llave administrativa guardada en Render. Nunca incluirla en JavaScript.
    if not enabled():
        raise HoldProblem("Piloto de retenciones deshabilitado.", 503)
    pilot_key = os.environ["PSICOLOGIA_HOLDS_PILOT_KEY"]
    given = request.headers.get("X-Pilot-Key", "")
    if not given or not hmac.compare_digest(given, pilot_key):
        raise HoldProblem("No autorizado.", 403)
    origin = request.headers.get("Origin")
    if origin and origin not in ("https://pscsantibanez.cl", "https://psicologia-carlos-demo.onrender.com"):
        raise HoldProblem("Origen no permitido.", 403)


def slot_window(service, day_text, hour_text, *, now=None):
    """Valida la hora desde el SERVIDOR; jamás acepta duración/precio del cliente."""
    if service not in CATALOG or not isinstance(day_text, str) or not isinstance(hour_text, str):
        raise HoldProblem("Servicio, fecha u hora inválidos.", 400)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day_text) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", hour_text):
        raise HoldProblem("Formato de fecha u hora inválido.", 400)
    try:
        day = date.fromisoformat(day_text)
        clock = time.fromisoformat(hour_text)
    except ValueError:
        raise HoldProblem("Fecha u hora inválida.", 400) from None
    minutes = clock.hour * 60 + clock.minute
    current = now if now is not None else datetime.now(TZ)
    current = current.astimezone(TZ)
    if (minutes < 9 * 60 or minutes % 15 != 0 or day > current.date() + timedelta(days=MAX_DAYS_AHEAD)):
        raise HoldProblem("Horario fuera del rango permitido.", 400)
    start = datetime.combine(day, clock, tzinfo=TZ)
    end = start + timedelta(minutes=CATALOG[service]["minutes"])
    if end.date() != day or end.hour * 60 + end.minute > 20 * 60 or start <= current:
        raise HoldProblem("La hora debe ser futura y terminar antes de las 20:00.", 400)
    return day, start, end


def overlaps(start, end, other_start, other_end):
    return start < other_end and other_start < end


def can_hold(active_windows, start, end, *, max_daily=MAX_DAILY):
    """Norma del calendario: máximo diario y ninguna superposición."""
    if len(active_windows) >= max_daily:
        return False
    return not any(overlaps(start, end, a, b) for a, b in active_windows)


def _calendar_free(start, end):
    # Leer el origen iCal FRESCO y bloquear si no puede consultarse. El feed de
    # Google podría publicar cambios con retraso: piloto, NO autorización real.
    from main import _read_busy_intervals, valid_calendar_url
    url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
    if not valid_calendar_url(url):
        raise HoldProblem("Calendario no configurado.", 503)
    try:
        intervals = _read_busy_intervals(url)
    except Exception:
        raise HoldProblem("No se pudo verificar el calendario.", 503) from None
    return not any(overlaps(start, end, a, b) for a, b in intervals)


def _database_connection():
    # psycopg se importa aquí para que el módulo no afecte el arranque del
    # backend público mientras la función piloto está desactivada.
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(
        os.environ["PSICOLOGIA_HOLDS_DATABASE_URL"],
        connect_timeout=6, row_factory=dict_row,
        application_name="psicologia-holds-pilot")


def _lock_day(conn, day):
    conn.execute(
        "INSERT INTO psychology_booking_days(slot_date) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
    conn.execute(
        "SELECT slot_date FROM psychology_booking_days WHERE slot_date=%s FOR UPDATE", (day,)).fetchone()


def _expire(conn, day):
    conn.execute(
        """UPDATE psychology_booking_holds SET status='EXPIRED', updated_at=now()
           WHERE slot_date=%s AND status='HELD' AND expires_at<=now()""",
        (day,))


def _active_windows(conn, day):
    rows = conn.execute(
        """SELECT starts_at, ends_at FROM psychology_booking_holds
           WHERE slot_date=%s AND
             (status='CONFIRMED' OR
              (status IN ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED')
               AND expires_at > now()))""", (day,)).fetchall()
    return [(r["starts_at"], r["ends_at"]) for r in rows]


def _new_hold(service, day, start, end):
    hold_id = uuid.uuid4()
    cancel_secret = secrets.token_urlsafe(32)
    digest = sha256(cancel_secret.encode()).hexdigest()
    try:
        with _database_connection() as conn:
            _lock_day(conn, day)
            _expire(conn, day)
            if not can_hold(_active_windows(conn, day), start, end):
                raise HoldProblem("El horario está retenido, ocupado o se alcanzó el máximo diario.", 409)
            conn.execute(
                """INSERT INTO psychology_booking_holds
                   (id,cancel_secret_sha256,slot_date,starts_at,ends_at,service,status,expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'HELD',now()+(%s * interval '1 second'))""",
                (hold_id, digest, day, start, end, service, HOLD_SECONDS))
    except HoldProblem:
        raise
    except Exception:
        raise HoldProblem("No se pudo guardar la retención temporal.", 503) from None
    return str(hold_id), cancel_secret


def _cancel_hold(hold_id, secret):
    if not isinstance(hold_id, str) or not isinstance(secret, str) or len(secret) > 150:
        raise HoldProblem("Identificador o credencial inválidos.", 400)
    try:
        parsed_id = uuid.UUID(hold_id)
    except ValueError:
        raise HoldProblem("Identificador de retención inválido.", 400) from None
    digest = sha256(secret.encode()).hexdigest()
    try:
        with _database_connection() as conn:
            row = conn.execute(
                "SELECT slot_date FROM psychology_booking_holds WHERE id=%s",
                (parsed_id,)).fetchone()
            if not row:
                raise HoldProblem("Retención no encontrada.", 404)
            _lock_day(conn, row["slot_date"])
            _expire(conn, row["slot_date"])
            hold = conn.execute(
                "SELECT cancel_secret_sha256,status FROM psychology_booking_holds WHERE id=%s FOR UPDATE",
                (parsed_id,)).fetchone()
            if not hold or not hmac.compare_digest(hold["cancel_secret_sha256"], digest):
                raise HoldProblem("Credencial incorrecta.", 403)
            if hold["status"] == "CANCELLED":
                return "CANCELLED"
            if hold["status"] != "HELD":
                raise HoldProblem("La retención ya no admite cancelación.", 409)
            conn.execute(
                """UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=now()
                   WHERE id=%s AND status='HELD'""", (parsed_id,))
            return "CANCELLED"
    except HoldProblem:
        raise
    except Exception:
        raise HoldProblem("No fue posible cancelar la retención.", 503) from None


@bp.errorhandler(HoldProblem)
def error_response(error):
    return jsonify(error=str(error)), error.status


@bp.get("/api/holds/pilot/config")
def config():
    return jsonify(status="pilot", enabled=enabled(),
                   hold_seconds=HOLD_SECONDS, max_daily=MAX_DAILY,
                   accepts_real_bookings=False, payments_enabled=False)


@bp.post("/api/holds/pilot/create")
def create():
    _authorize()
    if not request.is_json or (request.content_length or 0) > 1024:
        raise HoldProblem("Solicitud JSON inválida.", 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise HoldProblem("Datos inválidos.", 400)
    service = data.get("service")
    day, start, end = slot_window(service, data.get("date"), data.get("time"))
    if not _calendar_free(start, end):
        raise HoldProblem("La hora está ocupada en Google Calendar.", 409)
    hold_id, cancel_secret = _new_hold(service, day, start, end)
    response = jsonify(status="HELD", hold_id=hold_id, cancel_secret=cancel_secret,
                       expires_in_seconds=HOLD_SECONDS,
                       service=service, date=day.isoformat(), time=start.strftime("%H:%M"))
    response.headers["Cache-Control"] = "no-store"
    return response, 201


@bp.post("/api/holds/pilot/cancel")
def cancel():
    _authorize()
    if not request.is_json or (request.content_length or 0) > 1024:
        raise HoldProblem("Solicitud JSON inválida.", 400)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise HoldProblem("Datos inválidos.", 400)
    return jsonify(status=_cancel_hold(data.get("hold_id"), data.get("cancel_secret")))
