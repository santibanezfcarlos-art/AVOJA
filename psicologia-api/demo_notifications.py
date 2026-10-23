"""Notificaciones del ensayo Webpay: propietario + destinatario de prueba.

Sin diagnóstico ni datos bancarios. Desactivadas salvo llave Resend y habilitación
explícita. Requiere que el destinatario coincida con la lista permitida del
servidor; el cliente nunca puede escoger receptores arbitrarios.
"""
import base64
from datetime import timezone
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


def template(role, order, service, start, end):
    """Texto mínimo: solo servicio y horario; sin datos sensibles."""
    service_name = {"psicoterapia": "Psicoterapia breve", "empleabilidad": "Asesoría en empleabilidad",
                    "competencias": "Potencia tus competencias"}[service]
    date_label = start.strftime("%d/%m/%Y")
    hour_label = start.strftime("%H:%M")
    end_label = end.strftime("%H:%M")
    intro = ("Recibiste una reserva de prueba en tu agenda." if role == "professional"
             else "Esta es tu confirmación de reserva de prueba.")
    subject = (("PRUEBA · Nueva reserva — " if role == "professional" else
                "PRUEBA · Confirmación de tu hora — ") + date_label + " " + hour_label)
    body = (
        "<div style='font-family:Arial,sans-serif;max-width:590px;color:#153e32'>"
        "<h2>Ps. Carlos Santibáñez F.</h2><p><strong>PRUEBA TÉCNICA — SIN COBRO REAL</strong></p>"
        f"<p>{escape(intro)}</p>"
        f"<p><strong>Servicio:</strong> {escape(service_name)}<br>"
        f"<strong>Fecha:</strong> {escape(date_label)}<br>"
        f"<strong>Horario (Chile):</strong> {escape(hour_label)}–{escape(end_label)}<br>"
        "<strong>Modalidad:</strong> Online</p>"
        f"<p><strong>Orden de prueba:</strong> {escape(order)}</p>"
        "<p>La transacción se procesó exclusivamente en el ambiente de "
        "integración de Transbank. No se emitió boleta ni se realizó cobro real.</p>"
        "<p>El enlace de videollamada y la agenda productiva se habilitarán "
        "cuando la plataforma entre en funcionamiento.</p></div>"
    )
    return subject, body


def send(role, order, hold_id, recipient, service, start, end, *, transport=None):
    if not configured():
        raise NotificationError("Envio de correo de prueba deshabilitado")
    email = allowed_email(recipient)
    professional = os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL", "").strip().lower()
    if (not email or role not in ("professional", "patient")
            or (role == "professional" and email != professional)):
        raise NotificationError("Destinatario no permitido")
    subject, body = template(role, order, service, start, end)
    attachment = {
        "filename": "reserva-prueba.ics",
        "content": base64.b64encode(ics_text(hold_id,start,end).encode("utf-8")).decode("ascii"),
        "content_type": "text/calendar",
    }
    payload = {"from": FROM, "to": [email], "subject": subject,
               "html": body, "attachments": [attachment]}
    if transport is not None:
        result = transport(payload)
    else:
        request = Request(
            RESEND_ENDPOINT, method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": "Bearer " + os.environ["PSICOLOGIA_PILOT_RESEND_API_KEY"],
                     "Content-Type": "application/json",
                     "Idempotency-Key": "psc-demo-" + order + "-" + role})
        try:
            with urlopen(request, timeout=12) as response:
                result = json.loads(response.read(10000).decode("utf-8"))
        except HTTPError as exc:
            provider_code = None
            try:
                response = json.loads(exc.read(1200).decode("utf-8"))
                if isinstance(response, dict):
                    provider_code = response.get("name")
            except (ValueError, UnicodeError, OSError):
                pass
            raise NotificationError("Resend rechazó el envío",
                http_status=exc.code,provider_code=provider_code) from None
        except (URLError, OSError, ValueError, TimeoutError) as exc:
            raise NotificationError("Resend no confirmó el envío",
                provider_code="transport_error") from None
    if not isinstance(result,dict) or not isinstance(result.get("id"),str):
        raise NotificationError("Respuesta de proveedor de correo inválida")
    return result["id"]
