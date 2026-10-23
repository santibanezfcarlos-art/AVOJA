"""Webpay Plus: ensayo aislado en el ambiente oficial de integración.

No captura datos bancarios, no registra pacientes ni genera reservas, boletas
o cobros reales. Nunca admite credenciales ni endpoints de producción.
"""
import html
import json
import os
import secrets
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Blueprint, Response, request

webpay_demo_bp = Blueprint("webpay_demo", __name__)

TEST_COMMERCE = "597055555532"
BASE = "https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions"
CHECKOUT_URL = "https://webpay3gint.transbank.cl/webpayserver/initTransaction"
RETURN_URL = "https://psicologia-carlos-calendar-api.onrender.com/api/webpay-demo/return"
SITE = "https://pscsantibanez.cl"
ALLOWED_ORIGINS = {SITE, "https://psicologia-carlos-demo.onrender.com"}
SERVICES = {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000}


def _enabled():
    return (os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") == "integration"
            and os.environ.get("PSICOLOGIA_WEBPAY_COMMERCE_CODE", "") == TEST_COMMERCE
            and bool(os.environ.get("PSICOLOGIA_WEBPAY_API_KEY", "")))


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
    if (request.content_length or 0) > 1024:
        return _page("Solicitud inválida", "Los datos enviados superan el límite.", status=400)
    service = request.form.get("service", "")
    if service not in SERVICES:
        return _page("Servicio no válido", "Selecciona un servicio disponible.", status=400)
    order = "PSCD" + secrets.token_hex(9).upper()
    session = "DEMO" + secrets.token_hex(12).upper()
    try:
        result = _send("POST", BASE, {
            "buy_order": order, "session_id": session,
            "amount": SERVICES[service], "return_url": RETURN_URL,
        })
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError):
        return _page("No fue posible iniciar la prueba", "Transbank no respondió correctamente. Intenta más tarde.", status=503)
    token = result.get("token")
    if result.get("url") != CHECKOUT_URL or not isinstance(token, str) or not (20 <= len(token) <= 150) or not token.isalnum():
        return _page("Respuesta inesperada", "No se abrió el formulario por seguridad.", status=503)
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
    # El retorno confirma una compra SIMULADA, sin ninguna asignación de reservas.
    if not _enabled():
        return _page("Prueba no habilitada", "El ambiente de integración no está configurado.", status=503)
    if request.values.get("TBK_TOKEN") is not None:
        return _page("Operación cancelada", "La prueba fue cancelada o interrumpida.")
    token = request.values.get("token_ws", "")
    if not isinstance(token, str) or not (20 <= len(token) <= 150) or not token.isalnum():
        return _page("Operación no finalizada", "Transbank no entregó un token válido.")
    try:
        result = _send("PUT", BASE + "/" + token)
    except (HTTPError, URLError, ValueError, KeyError, TimeoutError):
        return _page("Resultado no confirmado", "No se pudo confirmar la respuesta de Transbank. No intentes usarla como reserva.", status=503)
    ok = (result.get("status") == "AUTHORIZED"
          and result.get("response_code") == 0
          and result.get("amount") in SERVICES.values()
          and isinstance(result.get("buy_order"), str)
          and result["buy_order"].startswith("PSCD"))
    if ok:
        return _page("¡Pago de prueba aprobado!", "Transbank confirmó una autorización simulada.")
    return _page("Pago de prueba rechazado", "La operación no fue aprobada en el ambiente de integración.")
