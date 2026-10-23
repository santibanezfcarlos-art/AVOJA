"""Webpay Plus: ensayo aislado en el ambiente oficial de integración.

No captura datos bancarios ni genera reservas o boletas. Guarda datos de
receptor solo si se solicita el historial privado configurado. No genera
cobros reales. Nunca admite credenciales ni endpoints de producción.
"""
import sqlite3
import billing
import webpay_demo_bridge as bridge
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Blueprint, Response, jsonify, redirect, request

webpay_demo_bp = Blueprint("webpay_demo", __name__)

TEST_COMMERCE = "597055555532"
BASE = "https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions"
CHECKOUT_URL = "https://webpay3gint.transbank.cl/webpayserver/initTransaction"
RETURN_URL = "https://psicologia-carlos-calendar-api.onrender.com/api/webpay-demo/return"
SITE = "https://pscsantibanez.cl"
ALLOWED_ORIGINS = {SITE, "https://psicologia-carlos-demo.onrender.com"}
SERVICES = {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000}
CODES = {"psicoterapia": "P", "empleabilidad": "E", "competencias": "C"}
CODE_SERVICES = {v: k for k, v in CODES.items()}
TZ = ZoneInfo("America/Santiago")
SIGNING_KEY_VAR = "PSICOLOGIA_WEBPAY_DEMO_SIGNING_KEY"


def _signing_key():
    key = os.environ.get(SIGNING_KEY_VAR, "")
    if len(key) < 40:
        raise ValueError("Demo signing secret is not configured")
    return key.encode("utf-8")


def _signed_session(service, day, hour):
    # Webpay limits session_id to 61 characters; no patient identifiers are included.
    compact_day = day.replace("-", "") if day else "00000000"
    compact_hour = hour.replace(":", "") if hour else "0000"
    body = "D2" + CODES[service] + compact_day + compact_hour + secrets.token_hex(4)
    signature = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()[:20]
    return body + signature


def _parse_session(value):
    if not isinstance(value, str) or not re.fullmatch(r"D2[PEC][0-9]{12}[a-f0-9]{28}", value):
        return None
    body, signature = value[:-20], value[-20:]
    expected = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()[:20]
    if not hmac.compare_digest(signature, expected):
        return None
    service = CODE_SERVICES[value[2]]
    day, clock = value[3:11], value[11:15]
    if day == "00000000" and clock == "0000":
        return {"service": service, "date": "", "time": ""}
    try:
        parsed_date = date.fromisoformat(day[:4] + "-" + day[4:6] + "-" + day[6:])
        parsed_time = datetime.strptime(clock, "%H%M")
    except ValueError:
        return None
    return {"service": service, "date": parsed_date.isoformat(),
            "time": parsed_time.strftime("%H:%M")}


def _signed_receipt(data):
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def _receipt_data(receipt):
    if not isinstance(receipt, str) or len(receipt) > 1200 or "." not in receipt:
        return None
    encoded, signature = receipt.rsplit(".", 1)
    if not re.fullmatch(r"[A-Za-z0-9_-]{30,1100}", encoded) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        return None
    expected = hmac.new(_signing_key(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    now = int(time.time())
    if (not isinstance(data, dict) or data.get("status") not in ("approved", "rejected")
        or data.get("service") not in SERVICES or data.get("amount") != SERVICES[data["service"]]
        or not isinstance(data.get("iat"), int) or not data["iat"] <= now <= data["iat"] + 3600
        or not isinstance(data.get("order"), str) or not re.fullmatch(r"PSCD[0-9A-F]{18}", data["order"])):
        return None
    return data


def _return_site(receipt=None, cancelled=False):
    query = ("?wp_demo=" + urlencode({"v": receipt})[2:] if receipt else
             "?wp_demo_cancelled=1" if cancelled else "")
    return redirect(SITE + "/" + query + "#agenda", code=303)



def _enabled():
    return (os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") == "integration"
            and os.environ.get("PSICOLOGIA_WEBPAY_COMMERCE_CODE", "") == TEST_COMMERCE
            and bool(os.environ.get("PSICOLOGIA_WEBPAY_API_KEY", ""))
            and len(os.environ.get(SIGNING_KEY_VAR, "")) >= 40)


def _send(method, url, data=None):
    key = os.environ["PSICOLOGIA_WEBPAY_API_KEY"]
    headers = {
        "Content-Type": "application/json",
        "Tbk-Api-Key-Id": TEST_COMMERCE,
        "Tbk-Api-Key-Secret": key,
    }
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    with urlopen(Request(url, method=method, headers=headers, data=payload), timeout=15) as response:
        return json.loads(response.read(16384).decode("utf-8"))


def _page(title, message, *, status=200):
    title = html.escape(title)
    message = html.escape(message)
    body = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} | Webpay de prueba</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f4f7f3;color:#173e33;margin:0;padding:7vh 18px}}
main{{max-width:590px;margin:auto;background:white;border:1px solid #dae8dd;border-radius:19px;padding:30px}}
h1{{line-height:1.2}}a{{color:#174e37;font-weight:700}}.note{{background:#ebf5eb;padding:15px;border-radius:11px}}</style>
</head><body><main><p>Ps. Carlos Santibáñez F. · Webpay Plus</p>
<h1>{title}</h1><p>{message}</p>
<p class="note"><strong>Ambiente de integración:</strong> no se realizó ningún cobro real, no se reservó una hora y no se emitió boleta.</p>
<p><a href="{SITE}/prueba-webpay.html">Volver a probar</a></p>
<p><a href="{SITE}/#agenda">Volver a la agenda</a></p></main></body></html>"""
    res = Response(body, status=status, content_type="text/html; charset=utf-8")
    res.headers["Cache-Control"] = "no-store"
    res.headers["Referrer-Policy"] = "no-referrer"
    res.headers["X-Content-Type-Options"] = "nosniff"
    return res


@webpay_demo_bp.post("/api/webpay-demo/start")
def start_demo():
    if not _enabled():
        return _page("Prueba no habilitada", "El ambiente de integración aún no está configurado.", status=503)
    if request.headers.get("Origin") not in ALLOWED_ORIGINS:
        return _page("Origen no permitido", "Inicia la prueba desde nuestra página web.", status=403)
    if (request.content_length or 0) > 4096:
        return _page("Solicitud inválida", "Los datos enviados superan el límite.", status=400)
    service = request.form.get("service", "")
    if service not in SERVICES:
        return _page("Servicio no válido", "Selecciona un servicio disponible.", status=400)
    day = request.form.get("date", "")
    hour = request.form.get("time", "")
    if bool(day) != bool(hour):
        return _page("Horario inválido", "Selecciona nuevamente la fecha y la hora.", status=400)
    if day and hour:
        try:
            chosen_day = date.fromisoformat(day)
            chosen_hour = datetime.strptime(hour, "%H:%M")
            start_time = datetime.combine(chosen_day, chosen_hour.time(), tzinfo=TZ)
            finish_time = start_time + timedelta(minutes={"psicoterapia": 45, "empleabilidad": 90, "competencias": 45}[service])
            if (start_time <= datetime.now(TZ) or chosen_day > datetime.now(TZ).date() + timedelta(days=60)
                or start_time.hour < 9 or start_time.minute % 15 != 0 or finish_time.date() != chosen_day or finish_time.hour > 20
                or (finish_time.hour == 20 and finish_time.minute > 0)):
                raise ValueError()
        except ValueError:
            return _page("Horario inválido", "Elige una fecha y hora futura válida.", status=400)
    person = None
    if request.form.get('billing_required') == '1':
        if not billing.configured():
            return _page('Historial no disponible', 'No se inició el pago porque falta activar el guardado privado de datos.', status=503)
        try:
            person = billing.receiver(request.form)
        except ValueError as exc:
            return _page('Revisa tus datos', str(exc), status=400)
    order = "PSCD" + secrets.token_hex(9).upper()
    session = _signed_session(service, day, hour)
    # El puente solo se activa cuando API/Render estén completamente configurados.
    # Si está activo, NO iniciamos un pago simulado sin reservar antes el cupo
    # en PostgreSQL y comprobar disponibilidad real en Google.
    if person and bridge.enabled():
        try:
            state=bridge.preflight(order=order,session=session,service=service,
                   day=day,hour=hour,amount=SERVICES[service],email=person["email"])
        except bridge.BridgeError as exc:
            if exc.state=="TEST_EMAIL_ONLY":
                return _page("Correo de prueba requerido",
                    "En integración solo se pueden enviar confirmaciones al correo de pruebas autorizado. "
                    "No ingreses información real de pacientes.",status=422)
            return _page("No se pudo reservar temporalmente",
                "No se inició Webpay porque la agenda no pudo confirmar el horario. "
                "Selecciona otra hora o revisa la conexión con Google Calendar.",status=503)
        if state!="READY":
            return _page("Horario no disponible",
               "No se inició el pago de prueba porque la hora no está libre o el servicio está temporalmente indisponible.",
               status=409)
    if person:
        try:
            billing.prepare(order, service, day, hour, SERVICES[service], person, 'integration')
        except (RuntimeError, sqlite3.Error, OSError):
            if bridge.enabled():
                try: bridge.complete(order=order,session=session,amount=SERVICES[service],outcome="cancelled")
                except bridge.BridgeError: pass
            return _page('No se guardaron los datos', 'No se inició el pago. Intenta más tarde.', status=503)
    try:
        result = _send("POST", BASE, {
            "buy_order": order, "session_id": session,
            "amount": SERVICES[service], "return_url": RETURN_URL,
        })
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError):
        if person and bridge.enabled():
            try: bridge.complete(order=order,session=session,amount=SERVICES[service],outcome="cancelled")
            except bridge.BridgeError: pass
        return _page("No fue posible iniciar la prueba", "Transbank no respondió correctamente. Intenta más tarde.", status=503)
    token = result.get("token")
    if result.get("url") != CHECKOUT_URL or not isinstance(token, str) or not (20 <= len(token) <= 150) or not token.isalnum():
        return _page("Respuesta inesperada", "No se abrió el formulario por seguridad.", status=503)
    if person:
        try:
            billing.bind_token(order, token)
        except (RuntimeError, sqlite3.Error, OSError):
            return _page('No se guardó la operación', 'No se abrió el pago. Intenta más tarde.', status=503)
    # No se muestran ni almacenan datos de tarjetas: Transbank los recoge en su propio dominio.
    body = ('<!doctype html><html lang="es"><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer">'
            '<title>Abriendo Webpay de prueba</title></head><body>'
            '<p>Abriendo el formulario oficial de Transbank (ambiente de integración)...</p>'
            '<form id="send" method="post" action="' + CHECKOUT_URL + '">'
            '<input type="hidden" name="token_ws" value="' + html.escape(token, quote=True) + '">'
            '<noscript><button type="submit">Continuar a Webpay</button></noscript>'
            '</form><script>document.getElementById("send").submit();</script></body></html>')
    res = Response(body, content_type="text/html; charset=utf-8")
    res.headers["Cache-Control"] = "no-store"
    res.headers["Referrer-Policy"] = "no-referrer"
    res.headers["Content-Security-Policy"] = "default-src 'none'; script-src 'unsafe-inline'; form-action " + CHECKOUT_URL
    return res


@webpay_demo_bp.route("/api/webpay-demo/return", methods=["GET", "POST"])
def return_demo():
    # El retorno informa de una compra SIMULADA. La web solo puede guardar bloqueos locales, nunca reservas reales.
    if not _enabled():
        return _page("Prueba no habilitada", "El ambiente de integración no está configurado.", status=503)
    if request.values.get("TBK_TOKEN") is not None:
        try:
            billing.cancelled(request.values.get('TBK_TOKEN', ''))
        except (RuntimeError, sqlite3.Error, OSError):
            pass  # Remains unconfirmed; never enters pending invoices.
        return _return_site(cancelled=True)
    token = request.values.get("token_ws", "")
    if not isinstance(token, str) or not (20 <= len(token) <= 150) or not token.isalnum():
        return _page("Operación no finalizada", "Transbank no entregó un token válido.")
    try:
        result = _send("PUT", BASE + "/" + token)
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError):
        return _page("Resultado no confirmado", "No se pudo confirmar la respuesta de Transbank. No intentes usarla como reserva.", status=503)
    parsed = _parse_session(result.get("session_id"))
    order = result.get("buy_order")
    if (not parsed or not isinstance(order, str) or not re.fullmatch(r"PSCD[0-9A-F]{18}", order)
        or result.get("amount") != SERVICES[parsed["service"]]):
        return _page("Resultado no confirmado", "La respuesta no coincide con una compra de prueba válida. No se simuló ninguna reserva.", status=503)
    approved = result.get("status") == "AUTHORIZED" and result.get("response_code") == 0
    try:
        saved = billing.record_result(order, token, result['amount'], approved, result.get('payment_type_code', ''))
    except (ValueError, RuntimeError, sqlite3.Error, OSError):
        return _page('Resultado pendiente de registro', 'La respuesta de Webpay no pudo guardarse. La operación requiere revisión; no repitas el pago.', status=503)
    if bridge.enabled():
        if not saved:
            bridge_state="RECONCILIATION_REQUIRED" if approved else "REJECTED"
        else:
            try:
                bridge_state=bridge.complete(order=order,session=result["session_id"],
                    amount=result["amount"],outcome="approved" if approved else "rejected")
            except bridge.BridgeError:
                bridge_state="RECONCILIATION_REQUIRED" if approved else "REJECTED"
    else:
        bridge_state="LEGACY"
    data = {"billing_saved": saved, "status": ("approved" if approved else "rejected"), "service": parsed["service"],
            "date": parsed["date"], "time": parsed["time"],
            "reservation_status": bridge_state,
            "amount": SERVICES[parsed["service"]], "order": order, "iat": int(time.time())}
    return _return_site(receipt=_signed_receipt(data))


@webpay_demo_bp.get("/api/webpay-demo/verify")
def verify_demo_result():
    # Confirmación criptográfica de resultado Transbank; nunca habilita una reserva real.
    if not _enabled():
        return jsonify(error="Prueba no disponible"), 503
    data = _receipt_data(request.args.get("receipt", ""))
    if data is None:
        return jsonify(error="Comprobante de prueba inválido o vencido"), 400
    return jsonify(data)

