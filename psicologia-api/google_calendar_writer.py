"""Google Calendar API (lectura inmediata y escritura de citas propias).

NO contiene rutas Flask; NO acepta datos personales, tarjetas o diagnósticos.
Usa una cuenta de servicio compartida EXPLÍCITAMENTE con el calendario elegido.
Su escritura está deshabilitada por defecto y no se invoca desde el checkout.
"""
from dataclasses import dataclass
from datetime import date, datetime, time
import json
import os
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
API_ROOT = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
SUMMARY = "Atención reservada · Carlos Santibáñez"
VALID_SERVICES = frozenset(("psicoterapia", "empleabilidad", "competencias"))


class CalendarIntegrationError(Exception):
    """Error público sin secretos, cuerpos HTTP ni detalles de eventos."""


class CalendarHttpError(CalendarIntegrationError):
    def __init__(self, status):
        self.status = status
        super().__init__("Google Calendar no pudo completar la solicitud")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class CalendarSettings:
    calendar_id: str
    service_account_info: dict | None
    write_enabled: bool

    @classmethod
    def from_environment(cls):
        ident = os.environ.get("PSICOLOGIA_GOOGLE_CALENDAR_ID", "").strip()
        raw = os.environ.get("PSICOLOGIA_GOOGLE_SERVICE_ACCOUNT_JSON", "")
        info = None
        if raw:
            try:
                info = json.loads(raw)
            except (ValueError, TypeError):
                raise CalendarIntegrationError("Credenciales Google inválidas") from None
            if not isinstance(info, dict) or info.get("type") != "service_account":
                raise CalendarIntegrationError("Tipo de credenciales Google inválido")
        if ident and (len(ident) > 320 or any(ord(c) < 33 for c in ident)):
            raise CalendarIntegrationError("Identificador de calendario inválido")
        return cls(ident, info,
                   os.environ.get("PSICOLOGIA_CALENDAR_WRITE_ENABLED") == "true")

    @property
    def configured(self):
        return bool(self.calendar_id and self.service_account_info)


def event_id_for_hold(hold_id):
    """Google admite minúsculas a-v y números 0-9: UUID hexadecimal cumple."""
    try:
        parsed = uuid.UUID(str(hold_id))
    except (ValueError, AttributeError, TypeError):
        raise CalendarIntegrationError("ID de retención inválido") from None
    return "psc" + parsed.hex


def _validate_time_window(start, end):
    if (not isinstance(start, datetime) or not isinstance(end, datetime)
            or not start.tzinfo or not end.tzinfo
            or end <= start or (end - start).total_seconds() > 24 * 3600):
        raise CalendarIntegrationError("Horario de evento inválido")


def _parse_edge(value):
    if not isinstance(value, dict):
        raise CalendarIntegrationError("Evento incompleto de Google")
    if "dateTime" in value:
        dt = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise CalendarIntegrationError("Evento sin zona horaria")
        return dt.astimezone(TZ)
    if "date" in value:
        d = date.fromisoformat(value["date"])
        return datetime.combine(d, time(0, 0), tzinfo=TZ)
    raise CalendarIntegrationError("Evento sin fecha")


def _owns(event, hold_id, *, start=None, end=None, service=None):
    if not isinstance(event, dict):
        return False
    ours = (event.get("extendedProperties") or {}).get("private") or {}
    if ours.get("psychology_hold_id") != str(uuid.UUID(str(hold_id))):
        return False
    if service is not None and ours.get("psychology_service") != service:
        return False
    try:
        if start is not None and _parse_edge(event["start"]) != start.astimezone(TZ):
            return False
        if end is not None and _parse_edge(event["end"]) != end.astimezone(TZ):
            return False
    except (KeyError, ValueError, TypeError, CalendarIntegrationError):
        return False
    return True


class CalendarWriter:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or CalendarSettings.from_environment()
        self.transport = transport or self._http_request

    def _check(self, *, write=False):
        if not self.settings.configured:
            raise CalendarIntegrationError("La API de Google Calendar aún no está configurada")
        if write and not self.settings.write_enabled:
            raise CalendarIntegrationError("Escritura de Google Calendar desactivada")
        if write and (os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") != "true"
                      or os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") != "disabled"
                      or os.environ.get("PSICOLOGIA_CHECKOUT_MODE", "disabled") != "disabled"):
            raise CalendarIntegrationError("Las escrituras solo pueden probarse en el piloto aislado")

    @property
    def _calendar_path(self):
        return "/calendars/" + quote(self.settings.calendar_id, safe="") + "/events"

    def _http_request(self, method, path, params, body):
        """Única conexión posible: Google APIs, jamás URLs del navegador."""
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request as AuthRequest
            creds = service_account.Credentials.from_service_account_info(
                self.settings.service_account_info, scopes=[SCOPE])
            creds.refresh(AuthRequest())
            query = ("?" + urlencode(params)) if params else ""
            req = Request(
                API_ROOT + path + query, method=method,
                data=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={"Authorization": "Bearer " + creds.token,
                         "Content-Type": "application/json", "Accept": "application/json"})
            with build_opener(_NoRedirect()).open(req, timeout=12) as resp:
                if method == "DELETE":
                    return {}
                raw = resp.read(300000)
            return json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            raise CalendarHttpError(exc.code) from None
        except (URLError, OSError, ValueError, KeyError, TypeError, ImportError):
            raise CalendarIntegrationError("No se pudo conectar a Google Calendar") from None
        except Exception:
            # Incluye fallos de firma OAuth; jamás registrar valores secretos.
            raise CalendarIntegrationError("Google Calendar no pudo autorizar la operación") from None

    def list_busy(self, start, end):
        """Lee ocurrencias vigentes (incluidas recurrentes y todo el día)."""
        self._check()
        _validate_time_window(start, end)
        base = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "singleEvents": "true", "showDeleted": "false", "maxResults": "2500"}
        busy = []
        token = None
        for _ in range(10):
            params = dict(base)
            if token:
                params["pageToken"] = token
            response = self.transport("GET", self._calendar_path, params, None)
            if not isinstance(response, dict) or not isinstance(response.get("items"), list):
                raise CalendarIntegrationError("Respuesta de calendario incompleta")
            for event in response["items"]:
                if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
                    continue
                try:
                    ev_start, ev_end = _parse_edge(event["start"]), _parse_edge(event["end"])
                except (ValueError, KeyError, TypeError):
                    raise CalendarIntegrationError("Google devolvió un evento sin horario válido") from None
                if ev_end > ev_start and start < ev_end and ev_start < end:
                    busy.append((ev_start, ev_end))
            token = response.get("nextPageToken")
            if not token:
                return busy
            if not isinstance(token, str) or len(token) > 2048:
                raise CalendarIntegrationError("Paginación inválida de Google")
        raise CalendarIntegrationError("Demasiados eventos para verificar disponibilidad")

    def is_free(self, start, end):
        return not bool(self.list_busy(start, end))

    def own_event_exists(self, *, hold_id, service, start, end):
        """Permite reconocer un evento creado si hubo pérdida del ACK de Google."""
        self._check()
        event_id=event_id_for_hold(hold_id)
        try:
            event=self.transport("GET",self._calendar_path+"/"+event_id,{},None)
        except CalendarHttpError as exc:
            if exc.status==404:
                return False
            raise
        return bool(event.get("id")==event_id and
                    _owns(event,hold_id,start=start,end=end,service=service))


    def create_paid_appointment(self, *, hold_id, service, start, end, payment_verified=False, demo_only=False, patient_name=None):
        """Uso interno FUTURO tras commit Transbank y comparación con BD persistente.

        El indicador payment_verified NO se obtiene del navegador y aún NO
        está conectado al flujo público. La prueba no debe escribir en el calendario.
        """
        self._check(write=True)
        if payment_verified is not True:
            raise CalendarIntegrationError("Falta confirmación bancaria desde el servidor")
        if service not in VALID_SERVICES:
            raise CalendarIntegrationError("Servicio no admitido")
        _validate_time_window(start, end)
        event_id = event_id_for_hold(hold_id)
        hold_id = str(uuid.UUID(str(hold_id)))
        body = {
            "id": event_id,
            "summary": (("PRUEBA TÉCNICA · Reserva simulada" if demo_only else SUMMARY) + (" - " + patient_name if patient_name else "")),
            "visibility": "private",
            "transparency": "opaque",
            "description": ("TRANSBANK INTEGRACIÓN: sin cobro real ni atención confirmada." if demo_only else "Reserva confirmada en la plataforma de Psicología. Sin datos personales."),
            "start": {"dateTime": start.isoformat(), "timeZone": "America/Santiago"},
            "end": {"dateTime": end.isoformat(), "timeZone": "America/Santiago"},
            "extendedProperties": {"private": {
                "psychology_hold_id": hold_id, "psychology_service": service}},
        }
        try:
            response = self.transport(
                "POST", self._calendar_path, {"sendUpdates": "none"}, body)
        except CalendarHttpError as error:
            if error.status != 409:
                raise
            # Reintento idempotente: solo aceptar un evento propio e idéntico.
            response = self.transport(
                "GET", self._calendar_path + "/" + event_id, {}, None)
        if (not isinstance(response, dict) or response.get("id") != event_id
                or not _owns(response, hold_id, start=start, end=end, service=service)):
            raise CalendarIntegrationError("No se pudo verificar la cita creada en Google")
        return event_id

    def reschedule_own_appointment(self, *, hold_id, service, old_start, old_end, new_start, new_end):
        """Change only a verified event owned by this reservation.

        Caller must atomically authorize the one-time change and reserve
        the destination slot before invoking this method. On failure, keep
        original booking; never delete and recreate the Google event.
        """
        self._check(write=True)
        if service not in VALID_SERVICES:
            raise CalendarIntegrationError("Servicio no admitido")
        _validate_time_window(old_start, old_end)
        _validate_time_window(new_start, new_end)
        if new_start == old_start:
            raise CalendarIntegrationError("Selecciona otro horario")
        event_id = event_id_for_hold(hold_id)
        path = self._calendar_path + "/" + event_id
        current = self.transport("GET", path, {}, None)
        if not _owns(current, hold_id, start=old_start, end=old_end, service=service):
            raise CalendarIntegrationError("La reserva original cambió en Google Calendar")
        # Se modifica el mismo evento; conserva su identidad y metadatos.
        body = {
            "start": {"dateTime": new_start.isoformat(), "timeZone": "America/Santiago"},
            "end": {"dateTime": new_end.isoformat(), "timeZone": "America/Santiago"},
        }
        updated = self.transport("PATCH", path, {"sendUpdates": "none"}, body)
        if not _owns(updated, hold_id, start=new_start, end=new_end, service=service):
            raise CalendarIntegrationError("No se pudo verificar el nuevo horario en Google Calendar")
        return event_id

    def cancel_own_appointment(self, hold_id):
        """Nunca borra eventos ajenos: comprueba metadatos privados de la reserva."""
        self._check(write=True)
        event_id = event_id_for_hold(hold_id)
        try:
            existing = self.transport("GET", self._calendar_path + "/" + event_id, {}, None)
        except CalendarHttpError as exc:
            if exc.status == 404:
                return False
            raise
        if not _owns(existing, hold_id):
            raise CalendarIntegrationError("Evento no pertenece a esta reserva")
        self.transport("DELETE", self._calendar_path + "/" + event_id,
                       {"sendUpdates": "none"}, None)
        return True
