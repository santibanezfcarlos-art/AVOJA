"""Notificaciones del ensayo Webpay: propietario + destinatario de prueba.

Sin diagnóstico ni datos bancarios. Desactivadas salvo llave Resend y habilitación
explícita. Requiere que el destinatario coincida con la lista permitida del
servidor; el cliente nunca puede escoger receptores arbitrarios.
"""
import base64
from datetime import timezone
from zoneinfo import ZoneInfo
from html import escape
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RESEND_ENDPOINT = "https://api.resend.com/emails"
FROM = "Ps. Carlos Santibáñez F. <contacto@pscsantibanez.cl>"
SENDER_NAME = "Ps. Carlos Santibáñez F."


class NotificationError(Exception):
    def __init__(self, message, *, http_status=None, provider_code=None):
        super().__init__(message)
        self.http_status = http_status
        self.provider_code = provider_code


def safe_error_label(exc):
    """Solo códigos de Resend y estado HTTP; nunca emails ni datos de solicitudes."""
    status = getattr(exc, "http_status", None)
    code = getattr(exc, "provider_code", None)
    status_label = ("HTTP_" + str(status)) if isinstance(status, int) else "NETWORK_OR_CONFIG"
    code_label = (code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,48}", code)
                  else "unspecified")
    return status_label + ":" + code_label


def allowed_email(email):
    """Valida destinatarios del ensayo; envío externo requiere habilitación específica.

    Por defecto solo acepta el buzón del propietario y alias Gmail.
    Con la opción externa activa admite cualquier email bien formado, siempre
    que se comprueben consentimiento y límites en el preflight del checkout.
    """
    candidate = (email or "").strip().lower()
    trusted = os.environ.get("PSICOLOGIA_PILOT_TEST_EMAIL", "").strip().lower()
    professional = os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL", "").strip().lower()
    if not trusted or not professional or professional != trusted or not candidate:
        return None
    if candidate == trusted:
        return candidate
    if trusted.endswith("@gmail.com"):
        local = trusted[:-len("@gmail.com")]
        prefix = local + "+"
        if candidate.startswith(prefix) and candidate.endswith("@gmail.com"):
            alias = candidate[len(prefix):-len("@gmail.com")]
            if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,31}", alias):
                return candidate
    if os.environ.get("PSICOLOGIA_PILOT_ALLOW_EXTERNAL_TEST_EMAILS") != "true":
        return None
    if len(candidate) > 254 or candidate.count("@") != 1:
        return None
    local, domain = candidate.split("@")
    if (not 1 <= len(local) <= 64 or local[0] == "." or local[-1] == "."
        or ".." in local or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)):
        return None
    if len(domain) > 253 or "." not in domain:
        return None
    labels = domain.split(".")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in labels):
        return None
    if len(labels[-1]) < 2:
        return None
    return candidate


def configured():
    return (os.environ.get("PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED") == "true"
            and os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") == "true"
            and os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") == "disabled"
            and os.environ.get("PSICOLOGIA_CHECKOUT_MODE", "disabled") == "disabled"
            and len(os.environ.get("PSICOLOGIA_PILOT_RESEND_API_KEY", "")) >= 24
            and bool(allowed_email(os.environ.get("PSICOLOGIA_PILOT_TEST_EMAIL"))))


def ics_text(hold_id, start, end):
    """Invitación de calendario de prueba sin datos clínicos."""
    if not start.tzinfo or not end.tzinfo or end <= start:
        raise NotificationError("Horario de invitación inválido")
    a = start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    b = end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//PSC Santibanez//Webpay Test//ES\r\n"
            "CALSCALE:GREGORIAN\r\nMETHOD:PUBLISH\r\nBEGIN:VEVENT\r\n"
            "UID:psc-test-" + str(hold_id) + "@pscsantibanez.cl\r\n"
            "DTSTAMP:" + a + "\r\nDTSTART:" + a + "\r\nDTEND:" + b + "\r\n"
            "SUMMARY:PRUEBA - Atención online Ps. Carlos Santibáñez\r\n"
            "DESCRIPTION:Simulación Transbank. NO corresponde a una sesión real.\r\n"
            "STATUS:TENTATIVE\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


def _brand():
    """Parámetros de presentación reutilizables para otros profesionales."""
    name = os.environ.get("PSICOLOGIA_BRAND_NAME", "Ps. Carlos Santibáñez Fica").strip()[:90]
    subtitle = os.environ.get("PSICOLOGIA_BRAND_SUBTITLE",
                              "Psicólogo · Atención online").strip()[:110]
    contact = os.environ.get("PSICOLOGIA_BRAND_CONTACT",
                             "contacto@pscsantibanez.cl").strip()[:120]
    website = os.environ.get("PSICOLOGIA_BRAND_WEBSITE",
                             "https://pscsantibanez.cl").strip()[:200]
    if not re.fullmatch(r"https://[a-z0-9.-]+(?:/[a-z0-9/_-]*)?", website, re.I):
        website = "https://pscsantibanez.cl"
    return name or "Ps. Carlos Santibáñez Fica", subtitle, contact, website


def _communication_copy():
    """Lectura opcional; un fallo del panel nunca bloquea un correo confirmado."""
    try:
        req=Request("https://psicologia-carlos-calendar-api.onrender.com/api/communications-public",
                    headers={"Accept":"application/json","User-Agent":"pscsantibanez-mailer/1.0"})
        with urlopen(req,timeout=3) as response:
            doc=json.loads(response.read(8000).decode("utf-8"))
        config=doc.get("communications",{})
        keys=("patient_heading","patient_message","patient_notes",
              "professional_heading","professional_message","professional_notes","signature")
        if all(isinstance(config.get(k),str) and 3<=len(config[k])<=700 for k in keys):
            return config
    except Exception:
        pass
    return {}


def template(role, order, service, start, end, *, correction=False, rebooking_url=None, rescheduled=False, patient_name=None, meet_url=None, demonstration=True):
    """Email institucional, seguro y adaptable al servicio y al profesional.

    Fechas siempre en Chile; sin información clínica ni URLs de sesión inventadas.
    """
    if role not in ("patient", "professional"):
        raise NotificationError("Rol de notificación inválido")
    services = {
        "psicoterapia": "Psicoterapia breve",
        "empleabilidad": "Asesoría en empleabilidad",
        "competencias": "Potencia tus competencias",
    }
    service_name = services[service]
    zone = ZoneInfo("America/Santiago")
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    date_label = local_start.strftime("%d/%m/%Y")
    hour_label = local_start.strftime("%H:%M")
    end_label = local_end.strftime("%H:%M")
    long_date = (f"{local_start.day} de "
                 + ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                    "julio", "agosto", "septiembre", "octubre", "noviembre",
                    "diciembre"][local_start.month - 1]
                 + f" de {local_start.year}")
    duration = round((end - start).total_seconds() / 60)
    name, subtitle, contact, website = _brand()
    is_pro = role == "professional"
    subject = (("Nueva sesión agendada · " if is_pro
                else "Tu hora está reservada · ")
               + service_name + " · " + date_label + " · " + hour_label + " h")
    if is_pro and patient_name:
        subject += " · " + patient_name
    if correction:
        subject = "CORRECCIÓN DE HORARIO · " + subject
    if rescheduled:
        subject = "TU ATENCIÓN HA SIDO REAGENDADA · " + service_name + " · " + date_label
    heading = "Tienes una nueva sesión agendada" if is_pro else "¡Tu hora está reservada!"
    if rescheduled:
        heading = "Atención reagendada" if is_pro else "¡Tu nueva hora está confirmada!"
    description = ("Se ha agendado una nueva atención en tu calendario. "
                   "Aquí tienes los detalles para organizar tu jornada."
                   if is_pro else
                   "¡Felicitaciones por dar este paso! Tomarte un tiempo "
                   "para ti y buscar orientación es una decisión importante. "
                   "Tu horario quedó reservado y estos son los detalles.")
    notes = ("La sesión ya figura en Google Calendar. "
             "Puedes revisar tu agenda para planificar la atención."
             if is_pro else
             "La atención será online. Te recomendamos buscar un lugar privado "
             "y tranquilo, con conexión estable, y conectarte unos minutos antes. "
             "Si necesitas instrucciones de conexión, contáctanos al correo indicado al final.")
    copy=_communication_copy()
    if copy:
        heading=copy["professional_heading" if is_pro else "patient_heading"]
        description=copy["professional_message" if is_pro else "patient_message"]
        notes=copy["professional_notes" if is_pro else "patient_notes"]
        name=copy["signature"]

    summary = hour_label + "–" + end_label
    correction_notice = (
        "<tr><td style='padding:15px 25px;background:#fff2e5;"
        "color:#754724;font-size:14px;line-height:1.5'>"
        "<strong>Corrección de horario:</strong> un correo anterior pudo "
        "mostrar la hora en UTC. Esta comunicación contiene el horario "
        "correcto de Chile.</td></tr>" if correction else "")
    def esc(value):
        return escape(str(value), quote=True)
    def field(label, value):
        return ("<tr><td style='padding:9px 0;color:#64796f;"
                "font-size:13px;vertical-align:top;width:37%'>"
                + esc(label) + "</td><td style='padding:9px 0;color:#163f34;"
                "font-size:15px;font-weight:600'>"+esc(value)+"</td></tr>")
    # A clickable Meet URL belongs inside the same appointment-details panel.
    # Escape data once, not the assembled HTML element.
    if meet_url:
        from urllib.parse import urlsplit
        parts = urlsplit(meet_url)
        if parts.scheme != "https" or parts.hostname != "meet.google.com":
            raise NotificationError("Enlace Meet inválido")
    meet_row = (
        "<tr><td style='padding:11px 0;color:#64796f;font-size:13px;vertical-align:top;width:37%'>"
        "Google Meet</td><td style='padding:11px 0;font-size:14px;font-weight:600;word-break:break-all'>"
        "<a href='" + esc(meet_url) + "' style='color:#176b58;text-decoration:underline'>"
        + esc(meet_url) + "</a></td></tr>" if meet_url else ""
    )
    table = "".join(([field("Paciente", patient_name)] if is_pro and patient_name else []) + [
        field("Servicio", service_name),
        field("Fecha", long_date),
        field("Hora (Chile)", summary),
        field("Duración", str(duration) + " minutos"),
        field("Modalidad", "Online"),
    ]) + meet_row
    from urllib.parse import urlsplit
    if rebooking_url is not None:
        parsed = urlsplit(rebooking_url)
        if role != "patient" or parsed.scheme != "https" or parsed.netloc not in ("pscsantibanez.cl", "psicologia-retenciones-piloto.onrender.com") or parsed.path != "/reagendar" or not parsed.query:
            raise NotificationError("Enlace de reagendamiento no permitido")
    rebooking_button = (
        "<tr><td style='padding:8px 30px 22px;text-align:center'>"
        "<a href='" + esc(rebooking_url) + "' style='display:inline-block;"
        "background:#235943;color:#fff;padding:15px 28px;border-radius:10px;"
        "font-weight:bold;text-decoration:none'>Reagendar mi atención</a>"
        "<p style='font-size:12px;color:#657c6e;line-height:1.6'>"
        "Una única reprogramación, con al menos 24 horas de anticipación.</p></td></tr>"
        if rebooking_url else ""
    )
    if meet_url:
        from urllib.parse import urlsplit
        parts = urlsplit(meet_url)
        if parts.scheme != "https" or parts.hostname != "meet.google.com":
            raise NotificationError("Enlace Meet inválido")
    intro = esc(description)
    html = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>" + esc(heading) + "</title></head>"
        "<body style='margin:0;padding:0;background:#f3f6f2;"
        "font-family:Arial,Helvetica,sans-serif;color:#173e32'>"
        "<div style='display:none;max-height:0;overflow:hidden;opacity:0'>"
        + esc(service_name + " · " + long_date + " · " + hour_label + " h")
        + "</div>"
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        "style='width:100%;background:#f3f6f2'><tr><td "
        "style='padding:32px 12px' align='center'>"
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        "style='width:100%;max-width:590px;background:#ffffff;"
        "border:1px solid #dbe7df;border-radius:18px;overflow:hidden'>"
        "<tr><td style='padding:27px 30px;background:#194a3a;color:white'>"
        "<div style='font-size:22px;font-weight:700;line-height:1.25'>"
        + esc(name) + "</div><div style='margin-top:5px;font-size:13px;"
        "color:#d3e6db'>" + esc(subtitle) + "</div></td></tr>"
        "<tr><td style='padding:18px 30px 2px;color:#4d8268;"
        "font-size:12px;font-weight:bold;letter-spacing:1.6px'>"
        + ("NUEVA ATENCIÓN AGENDADA" if is_pro else "CONFIRMACIÓN DE RESERVA") + "</td></tr>"
        + correction_notice +
        "<tr><td style='padding:12px 30px 4px'>"
        "<h1 style='font-size:27px;line-height:1.2;margin:0;color:#194a3a'>"
        + esc(heading) + "</h1><p style='font-size:15px;"
        "line-height:1.7;color:#49695b;margin:17px 0 0'>"
        + intro + "</p></td></tr>"
        "<tr><td style='padding:15px 30px'>"
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        "style='width:100%;border:1px solid #dce9df;"
        "border-radius:12px;background:#f7faf7'>"
        "<tr><td style='padding:16px 20px'>"
        "<table role='presentation' cellpadding='0' cellspacing='0' "
        "style='width:100%;border-collapse:collapse'>"
        + table + "</table></td></tr></table></td></tr>"
        "<tr><td style='padding:2px 30px 22px;font-size:14px;"
        "line-height:1.7;color:#4b665a'>"
        + esc(notes) + "<p style='margin:15px 0 0'>"
        "<span style='font-size:12px;color:#71867a'>"
        + ("Reserva realizada en ambiente de demostración: el pago es simulado y no se ha emitido boleta." if demonstration else "Atención confirmada con bono Fonasa.") + "</span>"
        "</p><p style='margin:14px 0 0'>"
        + ("Adjuntamos una invitación de calendario para que puedas incorporar el horario a tu agenda." if demonstration else ("La atención ya figura en tu calendario." if is_pro else "Puedes guardar tu atención usando el archivo de calendario adjunto.")) + "</p>"
        "</td></tr>"
        + rebooking_button +
        "<tr><td style='background:#eef4ed;border-top:1px solid #dce9df;"
        "padding:20px 30px;font-size:12px;line-height:1.7;"
        "color:#577166'><strong style='color:#194a3a'>"
        + esc(name) + "</strong><br>"
        + esc(contact) + " · <a href='" + esc(website)
        + "' style='color:#276b50;text-decoration:underline'>"
        + esc(website.replace("https://", "")) + "</a><br>"
        "Notificación automática de reserva. No incluye antecedentes clínicos."
        "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, html


def send(role, order, hold_id, recipient, service, start, end, *, transport=None, correction=False, rebooking_url=None, rescheduled=False, patient_name=None, meet_url=None):
    if not configured():
        raise NotificationError("Envio de correo de prueba deshabilitado")
    email = allowed_email(recipient)
    professional = os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL", "").strip().lower()
    if (not email or role not in ("professional", "patient")
            or (role == "professional" and email != professional)):
        raise NotificationError("Destinatario no permitido")
    subject, body = template(role, order, service, start, end, correction=correction, rebooking_url=rebooking_url, rescheduled=rescheduled, patient_name=patient_name, meet_url=meet_url)
    attachment = {
        "filename": "reserva.ics",
        "content": base64.b64encode(ics_text(hold_id,start,end).encode("utf-8")).decode("ascii"),
        "content_type": "text/calendar",
    }
    local_start=start.astimezone(ZoneInfo("America/Santiago"))
    local_end=end.astimezone(ZoneInfo("America/Santiago"))
    service_title={"psicoterapia":"Psicoterapia breve",
                   "empleabilidad":"Asesoría en empleabilidad",
                   "competencias":"Potencia tus competencias"}[service]
    name, subtitle, contact, website = _brand()
    plain = (("Tienes una nueva sesión agendada" if role=="professional" else
              "¡Felicitaciones por dar este paso! Tu hora está reservada.") + "\n"
             + "Profesional: " + name + "\n"
             + "Servicio: " + service_title + "\n"
             + "Fecha: " + local_start.strftime("%d/%m/%Y") + "\n"
             + "Hora (Chile): " + local_start.strftime("%H:%M") + "–"
             + local_end.strftime("%H:%M") + "\n"
             + ("Paciente: " + patient_name + "\n" if role=="professional" and patient_name else "")
             + "Modalidad: Online\n"
             + "Pago simulado en ambiente de demostración; no se emitió boleta.\n"
             + ("Reagendar mi atención: " + rebooking_url + "\n" if rebooking_url else "")
             + ("Google Meet: " + meet_url + "\n" if meet_url else "")
             + "Contacto: " + contact + "\n" + website)
    payload = {"from": FROM, "to": [email], "subject": subject,
               "html": body, "text": plain, "attachments": [attachment]}
    return send_payload(payload, "psc-demo-" + order + "-" + role
                        + ("-tz-correction-v1" if correction else "")
                        + ("-rescheduled-v1" if rescheduled else ""), transport=transport)


def send_payload(payload, idempotency_key, *, transport=None):
    """Shared corporate Resend transport; never logs request data or credentials."""
    if transport is not None:
        result = transport(payload)
    else:
        request = Request(
            RESEND_ENDPOINT, method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + os.environ["PSICOLOGIA_PILOT_RESEND_API_KEY"],
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "pscsantibanez-reservas/1.0",
                     "Idempotency-Key": idempotency_key})
        try:
            with urlopen(request, timeout=12) as response:
                result = json.loads(response.read(10000).decode("utf-8"))
        except HTTPError as exc:
            provider_code = None
            try:
                body = exc.read(2000).decode("utf-8", "replace")
                response = json.loads(body)
                if isinstance(response, dict):
                    message = str(response.get("message", "") or response.get("error", ""))
                    raw_code = response.get("name") or response.get("code")
                    if isinstance(raw_code, str) and re.fullmatch(r"[a-z_]{1,48}", raw_code):
                        provider_code = raw_code
                    else:
                        lowercase = message.lower()
                        if "domain" in lowercase and ("verif" in lowercase or "authoriz" in lowercase):
                            provider_code = "sender_domain_not_verified"
                        elif "only send" in lowercase or "testing" in lowercase:
                            provider_code = "account_testing_restriction"
                        elif "key" in lowercase or "permission" in lowercase or "restricted" in lowercase:
                            provider_code = "api_key_permission"
                        elif "blocked" in lowercase or "suspend" in lowercase:
                            provider_code = "account_sending_blocked"
                        else:
                            provider_code = "provider_rejected"
                else:
                    provider_code = "unexpected_error_format"
            except (ValueError, OSError):
                # Respuestas HTML/texto (p.ej., pasarela de red) se clasifican
                # sin registrar el cuerpo, que podría incluir datos privados.
                kind=exc.headers.get("Content-Type", "").lower()
                if "text/html" in kind:
                    provider_code="upstream_html_block"
                elif "text/plain" in kind:
                    provider_code="upstream_text_block"
                elif "application/json" in kind:
                    provider_code="malformed_json_response"
                else:
                    provider_code="unknown_403_response"
            raise NotificationError("Resend rechazó el envío",
                http_status=exc.code,provider_code=provider_code) from None
        except (URLError, OSError, ValueError, TimeoutError) as exc:
            raise NotificationError("Resend no confirmó el envío",
                provider_code="transport_error") from None
    if not isinstance(result,dict) or not isinstance(result.get("id"),str):
        raise NotificationError("Respuesta de proveedor de correo inválida")
    return result["id"]
