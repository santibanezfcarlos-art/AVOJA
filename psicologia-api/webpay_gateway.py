"""Pasarela Webpay Plus para la futura agenda online de Psicología.

NO HAY RUTAS DE COBRO NI RESERVAS HABILITADAS EN ESTE MÓDULO.
La creación/confirmación de pagos debe enlazarse posteriormente a transacciones
atómicas de una base de datos duradera. No usar con pacientes antes de ello.
"""
import json
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


SESSION_PRICE_CLP = 30_000
SESSION_DURATION_MIN = 60
DAILY_BOOKING_LIMIT = 3
HOLD_DURATION_SECONDS = 600


class WebpayError(Exception):
    """Fallo seguro sin credenciales ni detalles bancarios sensibles."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def approved_payment(result, *, buy_order, session_id, amount=SESSION_PRICE_CLP):
    """Comprobar resultado servidor-a-servidor contra orden persistida.

    Aprobar en la base de datos solamente una vez y dentro de una transacción.
    """
    if not isinstance(result, dict) or type(amount) is not int or amount != SESSION_PRICE_CLP:
        return False
    try:
        provider_amount = Decimal(str(result.get("amount", "")))
    except (InvalidOperation, ValueError):
        return False
    return (
        result.get("status") == "AUTHORIZED"
        and type(result.get("response_code")) is int
        and result["response_code"] == 0
        and result.get("buy_order") == buy_order
        and result.get("session_id") == session_id
        and provider_amount == Decimal(SESSION_PRICE_CLP)
    )


class WebpayGateway:
    """Wrapper para la API REST oficial Webpay Plus v1.2.

    El ambiente queda DESHABILITADO de forma predeterminada.
    Nunca crear transacciones sin un hold persistido y confirmado en servidor.
    """

    TEST_CODE = "597055555532"
    # Clave PÚBLICA de integración documentada por Transbank, no credencial productiva.
    TEST_KEY = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"
    API_PATH = "/rswebpaytransaction/api/webpay/v1.2/transactions"
    CHECKOUT_PATH = "/webpayserver/initTransaction"

    def __init__(self, *, mode=None, public_url=None):
        self.mode = (mode if mode is not None else os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled")).lower()
        if self.mode not in {"disabled", "integration", "production"}:
            raise ValueError("PSICOLOGIA_WEBPAY_MODE inválido")
        self.public_url = (public_url if public_url is not None
                           else os.environ.get("PSICOLOGIA_API_PUBLIC_URL", "https://psicologia-carlos-calendar-api.onrender.com")).rstrip("/")
        u = urlsplit(self.public_url)
        if (u.scheme != "https" or not u.hostname or u.username or u.password
                or u.query or u.fragment or u.path not in {"", "/"}):
            raise ValueError("PSICOLOGIA_API_PUBLIC_URL requiere un origen HTTPS")
        if self.mode == "production":
            if os.environ.get("PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED") != "true":
                raise ValueError("Producción requiere autorización explícita")
            self.commerce_code = os.environ.get("PSICOLOGIA_WEBPAY_COMMERCE_CODE", "").strip()
            self.api_key = os.environ.get("PSICOLOGIA_WEBPAY_API_KEY", "").strip()
            if not re.fullmatch(r"\d{12}", self.commerce_code) or not self.api_key or len(self.api_key) > 256:
                raise ValueError("Credenciales de producción ausentes o inválidas")
            self.host = "webpay3g.transbank.cl"
        else:
            self.commerce_code, self.api_key = self.TEST_CODE, self.TEST_KEY
            self.host = "webpay3gint.transbank.cl"
        self.endpoint = f"https://{self.host}{self.API_PATH}"
        self.opener = build_opener(_NoRedirect())

    def validate_checkout_url(self, url):
        if not isinstance(url, str):
            return False
        try:
            parsed = urlsplit(url)
            return (parsed.scheme == "https" and parsed.hostname == self.host
                    and parsed.port in {None, 443} and not parsed.username
                    and not parsed.password and not parsed.query
                    and not parsed.fragment and parsed.path == self.CHECKOUT_PATH)
        except ValueError:
            return False

    def _request(self, method, *, token=None, data=None):
        if self.mode == "disabled":
            raise WebpayError("Pagos no habilitados")
        if token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
            raise WebpayError("Token Webpay inválido")
        path = self.endpoint + ("/" + token if token else "")
        request = Request(
            path, method=method,
            data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers={
                "Content-Type": "application/json",
                "Tbk-Api-Key-Id": self.commerce_code,
                "Tbk-Api-Key-Secret": self.api_key,
            },
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read(100_001))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (HTTPError, URLError, TimeoutError, ValueError, OSError):
            raise WebpayError("No se pudo consultar Transbank. Verificar el estado antes de reintentar.") from None

    def create_for_existing_hold(self, *, buy_order, session_id):
        """Crear solo tras bloquear cupo en BD y asociar orden/idempotencia."""
        if not isinstance(buy_order, str) or not re.fullmatch(r"PSI[A-Z0-9]{8,22}", buy_order):
            raise WebpayError("Orden de compra inválida")
        if not isinstance(session_id, str) or not re.fullmatch(r"PSIS[A-Z0-9]{8,40}", session_id):
            raise WebpayError("Identificador de sesión inválido")
        resp = self._request("POST", data={
            "buy_order": buy_order,
            "session_id": session_id,
            "amount": SESSION_PRICE_CLP,
            "return_url": self.public_url + "/api/webpay/return",
        })
        if (not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", str(resp.get("token", "")))
                or not self.validate_checkout_url(resp.get("url"))):
            raise WebpayError("Transbank devolvió datos de checkout inesperados")
        return {"token": resp["token"], "url": resp["url"]}

    def commit(self, token):
        """Usar solo una vez por token, bajo bloqueo de una transacción persistente."""
        return self._request("PUT", token=token, data={})

    def status(self, token):
        """Consulta de conciliación; no autoriza por sí sola una reserva."""
        return self._request("GET", token=token)
