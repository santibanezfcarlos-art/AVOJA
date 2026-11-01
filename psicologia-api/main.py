"""Consulta privada de Google Calendar para la agenda demostrativa de Psicología.

Configurar únicamente en Render GOOGLE_CALENDAR_ICS_URL (URL privada iCal de Google).
Esta aplicación NO expone descripciones, asistentes ni títulos de eventos.
No guarda reservas ni acepta pagos.
"""
import os
import time
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from flask import Flask, jsonify, request, redirect
from icalendar import Calendar
import recurring_ical_events

app = Flask(__name__)
class CalendarConfigurationError(Exception):
    """Error seguro de configuración; no divulgar URL ni token de iCal."""
    pass

def valid_calendar_url(url):
    if not url:
        return False
    parts = urlparse(url.strip())
    return (parts.scheme == "https" and parts.hostname == "calendar.google.com"
            and parts.port in (None, 443)
            and parts.path.startswith("/calendar/ical/") and parts.path.endswith(".ics"))

TZ = ZoneInfo("America/Santiago")
SLOT_DURATION_MINUTES = 60
OPEN_MINUTES = 9 * 60
CLOSE_MINUTES = 20 * 60
CACHE_TTL_SECONDS = 600
ALLOWED_SITE = "https://pscsantibanez.cl"
ALLOWED_ORIGINS = frozenset((ALLOWED_SITE, "https://pscsantibanez.cl"))
MAX_FEED_SIZE = 24_000_000
_feed_cache = {"at": 0.0, "events": []}
_lock = threading.Lock()
_refresh_done = threading.Event()
_refreshing = False
_retry_after = 0.0
CALENDAR_WAIT_SECONDS = 15
CALENDAR_RETRY_SECONDS = 15


class CalendarRefreshingError(Exception):
    """La agenda no está verificada todavía; no ofrecer disponibilidad."""
    pass
# Estado seguro de configuración, sin incluir URL, token ni eventos en los registros.
_calendar_feed_url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
app.logger.warning("Calendar iCal configurado=%s formato_valido=%s",
                   bool(_calendar_feed_url), valid_calendar_url(_calendar_feed_url))



def _to_local(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
    return datetime(value.year, value.month, value.day, tzinfo=TZ)


def _read_busy_intervals(url):
    """Red y procesamiento fuera del bloqueo compartido de la caché."""
    req = Request(url, headers={"User-Agent": "PsychologyCalendarAvailability/1.0"})
    with urlopen(req, timeout=12) as response:
        # No seguir URLs arbitrarias por redirección.
        if urlparse(response.geturl()).hostname != "calendar.google.com":
            raise CalendarConfigurationError("Redirección de calendario no permitida")
        raw = response.read(MAX_FEED_SIZE + 1)
    if len(raw) > MAX_FEED_SIZE:
        app.logger.warning("CALENDAR_READ_STAGE: size_exceeded")
        raise ValueError("Calendario demasiado grande")
    try:
        cal = Calendar.from_ical(raw)
    except ValueError:
        app.logger.warning("CALENDAR_READ_STAGE: parse_ical_failed")
        raise
    # Se consultan los próximos 70 días; Google iCal admite recurrencias.
    start = datetime.now(TZ).date() - timedelta(days=1)
    end = start + timedelta(days=72)
    start_dt = datetime(start.year, start.month, start.day, tzinfo=TZ)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=TZ)
    try:
        events = recurring_ical_events.of(cal).between(start_dt, end_dt)
    except ValueError:
        app.logger.warning("CALENDAR_READ_STAGE: expand_recurrences_failed")
        raise
    intervals = []
    for event in events:
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            continue
        if "DTSTART" not in event:
            continue
        try:
            s = _to_local(event.decoded("DTSTART"))
            if "DTEND" in event:
                e = _to_local(event.decoded("DTEND"))
            elif "DURATION" in event:
                e = s + event.decoded("DURATION")
            else:
                e = s + (timedelta(days=1) if isinstance(event.decoded("DTSTART"), date) and not isinstance(event.decoded("DTSTART"), datetime) else timedelta(hours=1))
        except ValueError:
            app.logger.warning("CALENDAR_READ_STAGE: event_date_failed")
            raise
        if e > s:
            intervals.append((s, e))
    return intervals


def _refresh_calendar(url):
    global _refreshing, _retry_after
    try:
        intervals = _read_busy_intervals(url)
        with _lock:
            _feed_cache.update(at=time.monotonic(), events=intervals)
            _retry_after = 0.0
        app.logger.info("CALENDAR_READ_CHECK: read_ok")
    except Exception as exc:
        # No divulgar URL privada ni contenidos de eventos.
        with _lock:
            _retry_after = time.monotonic() + CALENDAR_RETRY_SECONDS
        app.logger.warning("CALENDAR_READ_CHECK: read_failed_%s", type(exc).__name__)
    finally:
        with _lock:
            _refreshing = False
            _refresh_done.set()


def _load_busy_intervals():
    global _refreshing
    url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
    if not url:
        return None
    if not valid_calendar_url(url):
        raise CalendarConfigurationError("Formato de dirección iCal incorrecto")
    with _lock:
        now = time.monotonic()
        if _feed_cache["at"] and now - _feed_cache["at"] < CACHE_TTL_SECONDS:
            return _feed_cache["events"]
        if not _refreshing:
            if now < _retry_after:
                raise CalendarRefreshingError()
            _refreshing = True
            _refresh_done.clear()
            threading.Thread(target=_refresh_calendar, args=(url,), daemon=True).start()
    # Nunca esperar indefinidamente ni servir datos vencidos como disponibilidad.
    _refresh_done.wait(timeout=CALENDAR_WAIT_SECONDS)
    with _lock:
        if _feed_cache["at"] and time.monotonic() - _feed_cache["at"] < CACHE_TTL_SECONDS:
            return _feed_cache["events"]
    raise CalendarRefreshingError()


@app.after_request
def _cors(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS and not request.path.startswith("/admin"):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        if request.path.startswith("/api/fonasa/book/"):
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/")
def index():
    """Al abrir la API en el navegador, mostrar el sitio web de Psicología."""
    return redirect(ALLOWED_SITE + "/", code=302)


@app.get("/health")
def health():
    url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
    from webpay_demo_bridge import enabled as checkout_bridge_ready
    from billing import configured as billing_ready
    return jsonify(status="ok", calendar_configured=bool(url), calendar_url_format_valid=valid_calendar_url(url),
                   webpay_demo_bridge_ready=checkout_bridge_ready(), billing_storage_ready=billing_ready(),
                   payments_simulated_only=not __import__('production_checkout').enabled())


@app.get("/api/availability")
def availability():
    # Los parámetros del paciente no deben confundirse con errores del calendario.
    try:
        first = date.fromisoformat(request.args.get("from", ""))
        last = date.fromisoformat(request.args.get("to", ""))
    except (TypeError, ValueError):
        return jsonify(error="Formato de fecha no válido"), 400

    try:
        duration = int(request.args.get("duration", "60"))
    except (TypeError, ValueError):
        return jsonify(error="Duración inválida"), 400
    if duration not in (45, 60, 90):
        return jsonify(error="Duración no permitida"), 400

    today = datetime.now(TZ).date()
    if first < today - timedelta(days=1) or last < first or last > today + timedelta(days=60) or (last - first).days > 21:
        return jsonify(error="Rango de fechas no permitido"), 400

    try:
        intervals = _load_busy_intervals()
    except CalendarRefreshingError:
        response = jsonify(status="refreshing", error="Actualizando disponibilidad. Reintentar en unos segundos.")
        response.headers["Retry-After"] = str(CALENDAR_RETRY_SECONDS)
        return response, 503
    except CalendarConfigurationError:
        app.logger.warning("GOOGLE_CALENDAR_ICS_URL no tiene un formato iCal válido. No se registran detalles.")
        return jsonify(status="not_configured", reason="calendar_url_invalid", configured=False, blocked={}, duration_minutes=duration)
    except Exception as exc:
        # No registrar URL privada ni detalles de eventos.
        app.logger.error("Fallo al procesar la agenda iCal: %s", type(exc).__name__)
        return jsonify(error="No fue posible verificar disponibilidad. Reintentar más tarde."), 503

    if intervals is None:
        return jsonify(status="not_configured", reason="calendar_url_missing", configured=False, blocked={}, duration_minutes=duration)

    # Google iCal can lag behind freshly created events; supplement its
    # intervals with the authoritative booking DB via the signed bridge.
    # Never display a slot as available if either source cannot be verified.
    from webpay_demo_bridge import enabled as bridge_ready, busy_intervals, BridgeError
    if bridge_ready():
        try:
            intervals = list(intervals) + busy_intervals(first,last,include_calendar=True)
        except (BridgeError,ValueError,TypeError):
            return jsonify(status="refreshing",
                error="Verificando las reservas recientes. Reintenta en unos segundos."),503

    from production_checkout import busy_intervals as production_busy
    from billing import configured as production_storage_ready
    if production_storage_ready():
        try:intervals=list(intervals)+production_busy(first,last)
        except Exception:return jsonify(error='No se pudo verificar la agenda de reservas.'),503
    # Pending Fonasa requests reserve capacity before Google publishes them.
    if production_storage_ready():
        try:
            from fonasa_internal import _schema
            import billing
            with billing.db() as con:
                _schema(con)
                pending=con.execute("""SELECT requested_date,requested_time FROM fonasa_intake
                    WHERE state IN ('appointment_requested','calendar_pending','calendar_confirmed')
                    AND requested_date>=? AND requested_date<=?""",
                    (first.isoformat(),last.isoformat())).fetchall()
            intervals=list(intervals)
            for item in pending:
                begins=datetime.fromisoformat(item['requested_date']+'T'+item['requested_time']).replace(tzinfo=TZ)
                intervals.append((begins,begins+timedelta(minutes=45)))
        except Exception:
            return jsonify(error='No se pudo verificar la agenda de bonos.'),503
    from daily_capacity import full_days
    exhausted=full_days(intervals)
    blocked = {}
    day = first
    while day <= last:
        items = []
        for minute in range(OPEN_MINUTES, CLOSE_MINUTES - duration + 1, 15):
            start = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=TZ)
            end = start + timedelta(minutes=duration)
            if day.weekday()==6 or (day.weekday()==5 and end.hour*60+end.minute>720) or day in exhausted or any(start < ev_end and ev_start < end for ev_start, ev_end in intervals):
                items.append(start.strftime("%H:%M"))
        blocked[day.isoformat()] = items
        day += timedelta(days=1)

    return jsonify(status="ok", configured=True, blocked=blocked,
                   duration_minutes=duration, timezone="America/Santiago")


# Iniciar la lectura solo desde una petición del worker. No crear hilos durante
# la importación: Gunicorn puede cargar la aplicación antes de hacer fork,
# y el hijo heredaría _refreshing=True sin heredar el hilo que lo resuelve.


# Prueba separada de Webpay Plus: sin cargos ni reservas reales.
from webpay_demo import webpay_demo_bp
app.register_blueprint(webpay_demo_bp)
from webpay_validation import bp as webpay_validation_bp
app.register_blueprint(webpay_validation_bp)
from production_checkout import bp as production_checkout_bp
app.register_blueprint(production_checkout_bp)
from billing import billing_bp
app.register_blueprint(billing_bp)
from sii_jobs import bp as sii_jobs_bp
app.register_blueprint(sii_jobs_bp)
from sii_cloud import bp as sii_cloud_bp
app.register_blueprint(sii_cloud_bp)
from sii_receipt_admin import bp as sii_receipt_admin_bp
app.register_blueprint(sii_receipt_admin_bp)
from sii_canary import bp as sii_canary_bp
app.register_blueprint(sii_canary_bp)
from admin import admin_bp
app.register_blueprint(admin_bp)
from reschedules import reschedules_bp
app.register_blueprint(reschedules_bp)
from fonasa_internal import bp as fonasa_private_bp
app.register_blueprint(fonasa_private_bp)
from fonasa_public import bp as fonasa_patient_bp
app.register_blueprint(fonasa_patient_bp)

# Solo booleans de configuración, nunca credenciales ni datos de pacientes.
from webpay_demo_bridge import enabled as _demo_bridge_is_ready
from billing import configured as _billing_is_ready
print("PSICOLOGIA_PUBLIC_DEMO_BRIDGE: " + ("ready" if _demo_bridge_is_ready() else "not_configured"),flush=True)
print("PSICOLOGIA_PUBLIC_BILLING: " + ("ready" if _billing_is_ready() else "not_configured"),flush=True)

def _diagnose_fonasa_slots_once():
    """Read-only, non-patient availability check. No calendar event details logged."""
    import logging
    import time as _time
    _time.sleep(4)
    try:
        from fonasa_internal import _slots, _schema, AvailabilityUnavailable
        import billing
        with billing.db() as connection:
            _schema(connection)
            available=_slots(connection,None,days=2)
        logging.getLogger("fonasa_availability").warning(
            "FONASA_SLOTS_STARTUP_PROBE status=ok days=%d slots=%d",
            len(available),sum(len(v) for v in available.values()))
    except Exception as exc:
        logging.getLogger("fonasa_availability").warning(
            "FONASA_SLOTS_STARTUP_PROBE status=failed reason=%s kind=%s",
            getattr(exc,"code","unexpected"),type(exc).__name__)

if os.environ.get("FONASA_SLOTS_DIAG_ON_START")=="yes":
    threading.Thread(target=_diagnose_fonasa_slots_once,daemon=True).start()


def _recover_one_existing_fonasa_booking():
    """One-shot operator-enabled recovery. No user identifiers are logged."""
    import time as _time
    import logging as _logging
    _time.sleep(8)
    logger=_logging.getLogger("fonasa_fulfillment")
    try:
        import billing
        from fonasa_internal import _schema, _confirm_fonasa_calendar, _deliver_pending_notifications
        with billing.db() as conn:
            _schema(conn)
            rows=conn.execute("""SELECT id FROM fonasa_intake
                WHERE requested_date=? AND requested_time=?
                AND state IN ('appointment_requested','calendar_pending','calendar_confirmed')""",
                ("2026-10-30","09:00")).fetchall()
        if len(rows)!=1:
            logger.warning("FONASA_RECOVERY state=skipped match_count=%d",len(rows))
            return
        outcome=_confirm_fonasa_calendar(rows[0]["id"])
        if outcome.get("state")=="calendar_confirmed":
            notices=_deliver_pending_notifications(rows[0]["id"])
            logger.warning("FONASA_RECOVERY state=calendar_confirmed patient_mail=%s coordinator_mail=%s",
                notices.get("patient","unknown"),notices.get("professional","unknown"))
        else:
            logger.warning("FONASA_RECOVERY state=%s reason=%s",
                outcome.get("state","unknown"),outcome.get("reason","unspecified"))
    except Exception as exc:
        logger.warning("FONASA_RECOVERY state=failed error_type=%s",type(exc).__name__)

if os.environ.get("FONASA_REPAIR_EXISTING_ON_START")=="yes":
    threading.Thread(target=_recover_one_existing_fonasa_booking,daemon=True).start()


def _recover_giovanni_unissued_sii_once():
    """Admin-authorized one-shot maintenance; does not issue or email."""
    import logging as _logging
    import time as _time
    _time.sleep(4)
    from sii_one_time_recovery import recover_once
    status=recover_once()
    from sii_one_time_recovery import dispatch_authorized_once, log_selected_state
    log_selected_state()
    dispatch_authorized_once(app)
    from sii_one_time_recovery import diagnose_authorized_once
    diagnose_authorized_once()
    _logging.getLogger("sii_invoice_recovery").warning(
        "PSC_SII_GIOVANNI_RECOVERY status=%s",status)


if os.environ.get("PSC_SII_UNLOCK_GIOVANNI_20261029")=="yes":
    threading.Thread(target=_recover_giovanni_unissued_sii_once,daemon=True).start()


def _recover_operator_confirmed_sii_20261030():
    """Fixed, expiring maintenance event; never starts an emission or email."""
    import logging
    import time
    time.sleep(4)
    from sii_one_time_recovery import recover_once, log_selected_state
    logger=logging.getLogger("sii_invoice_recovery")
    logger.warning("PSC_SII_CONFIRMED_RECOVERY phase=before order_suffix=155ECB")
    log_selected_state()
    status=recover_once(confirmed_event=True)
    logger.warning("PSC_SII_CONFIRMED_RECOVERY status=%s order_suffix=155ECB",status)
    log_selected_state()


if os.environ.get("RENDER"):
    threading.Thread(target=_recover_operator_confirmed_sii_20261030,daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))





# Recover only pre-claim startup failures; no issuance side effects.
def _recover_sii_preclaims():
    import time as _time
    _time.sleep(4)
    from sii_dispatch import recover_stalled_preclaims
    recover_stalled_preclaims()
if os.environ.get("RENDER"):
    threading.Thread(target=_recover_sii_preclaims,daemon=True).start()

