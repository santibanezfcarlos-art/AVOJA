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
ALLOWED_SITE = "https://psicologia-carlos-demo.onrender.com"
MAX_FEED_SIZE = 24_000_000
_feed_cache = {"at": 0.0, "events": []}
_lock = threading.Lock()
# Estado seguro de configuración, sin incluir URL, token ni eventos en los registros.
_calendar_feed_url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
app.logger.warning("Calendar iCal configurado=%s formato_valido=%s",
                   bool(_calendar_feed_url), valid_calendar_url(_calendar_feed_url))



def _to_local(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
    return datetime(value.year, value.month, value.day, tzinfo=TZ)


def _load_busy_intervals():
    url = os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip()
    if not url:
        return None
    if not valid_calendar_url(url):
        raise CalendarConfigurationError("Formato de dirección iCal incorrecto")
    now = time.monotonic()
    with _lock:
        if now - _feed_cache["at"] < CACHE_TTL_SECONDS:
            return _feed_cache["events"]
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
        _feed_cache.update(at=now, events=intervals)
        return intervals


@app.after_request
def _cors(response):
    if request.headers.get("Origin") == ALLOWED_SITE:
        response.headers["Access-Control-Allow-Origin"] = ALLOWED_SITE
        response.headers["Vary"] = "Origin"
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
    return jsonify(status="ok", calendar_configured=bool(url), calendar_url_format_valid=valid_calendar_url(url))


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
    except CalendarConfigurationError:
        app.logger.warning("GOOGLE_CALENDAR_ICS_URL no tiene un formato iCal válido. No se registran detalles.")
        return jsonify(status="not_configured", reason="calendar_url_invalid", configured=False, blocked={}, duration_minutes=duration)
    except Exception as exc:
        # No registrar URL privada ni detalles de eventos.
        app.logger.error("Fallo al procesar la agenda iCal: %s", type(exc).__name__)
        return jsonify(error="No fue posible verificar disponibilidad. Reintentar más tarde."), 503

    if intervals is None:
        return jsonify(status="not_configured", reason="calendar_url_missing", configured=False, blocked={}, duration_minutes=duration)

    blocked = {}
    day = first
    while day <= last:
        items = []
        for minute in range(OPEN_MINUTES, CLOSE_MINUTES - duration + 1, 15):
            start = datetime(day.year, day.month, day.day, minute // 60, minute % 60, tzinfo=TZ)
            end = start + timedelta(minutes=duration)
            if any(start < ev_end and ev_start < end for ev_start, ev_end in intervals):
                items.append(start.strftime("%H:%M"))
        blocked[day.isoformat()] = items
        day += timedelta(days=1)

    return jsonify(status="ok", configured=True, blocked=blocked,
                   duration_minutes=duration, timezone="America/Santiago")


def _startup_calendar_diagnostic():
    """Prueba de salud: nunca imprimir contenidos ni secretos del calendario."""
    try:
        values = _load_busy_intervals()
        app.logger.warning("CALENDAR_READ_CHECK: %s", "not_configured" if values is None else "read_ok")
    except CalendarConfigurationError:
        app.logger.warning("CALENDAR_READ_CHECK: invalid_ics_source")
    except Exception as exc:
        app.logger.warning("CALENDAR_READ_CHECK: read_failed_%s", type(exc).__name__)

# Ejecutar la prueba una sola vez durante el arranque, sin bloquear el servidor.
if os.environ.get("GOOGLE_CALENDAR_ICS_URL", "").strip():
    threading.Thread(target=_startup_calendar_diagnostic, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
