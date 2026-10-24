"""Adaptador aislado de Webpay Plus para la futura reserva pagada de Psicología.

Inspirado en la integración de Valhalla: configuración separada por ambiente,
API de Transbank consultada exclusivamente desde backend, validación estricta,
y sin acreditar por simples parámetros de retorno del navegador.

ESTE MÓDULO NO REGISTRA RUTAS FLASK NI HACE COBROS POR SÍ SOLO.
No habilitar cobros hasta tener retenciones duraderas e idempotentes,
calendario escribible, y conciliación verificada extremo a extremo.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

INTEGRATION_HOST = "webpay3gint.transbank.cl"
PRODUCTION_HOST = "webpay3g.transbank.cl"
INTEGRATION_CODE = "597055555532"
INTEGRATION_API_KEY = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"
API_PATH = "/rswebpaytransaction/api/webpay/v1.2/transactions"
CHECKOUT_PATH = "/webpayserver/initTransaction"
AMOUNTS = {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000}


class GatewayError(RuntimeError):
    """Error controlado; nunca expone claves, tokens ni datos de tarjetas."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class GatewaySettings:
    environment: str
    commerce_code: str
    api_key: str
    host: str
    return_url: str

    @classmethod
    def from_environment(cls, *, mode=None):
        # Nombre propio: no altera PSICOLOGIA_WEBPAY_MODE usado por el demo.
        selected = (mode if mode is not None else os.environ.get(
            "PSICOLOGIA_CHECKOUT_MODE", "disabled")).strip().lower()
        if selected not in ("disabled", "integration", "production"):
            raise GatewayError("Modo de pagos no admitido")
        public_url = os.environ.get(
            "PSICOLOGIA_API_PUBLIC_URL", "https://psicologia-carlos-calendar-api.onrender.com").rstrip("/")
        parts = urlsplit(public_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or parts.path not in ("", "/")
                or parts.query or parts.fragment):
            raise GatewayError("Se requiere el dominio HTTPS del backend")
        return_url = public_url + "/api/webpay/return"
        if selected == "production":
            # El código comercial NO basta. Exigimos llave API separada y autorización explícita.
            if os.environ.get("PSICOLOGIA_PRODUCTION_APPROVED") != "true":
                raise GatewayError("El ambiente productivo aún no está autorizado")
            code = os.environ.get("PSICOLOGIA_PRODUCTION_COMMERCE_CODE", "").strip()
            key = os.environ.get("PSICOLOGIA_PRODUCTION_API_KEY", "").strip()
            if (not re.fullmatch(r"[0-9]{12}", code) or not 16 <= len(key) <= 256
                    or any(char.isspace() for char in key)):
                raise GatewayError("Faltan credenciales productivas válidas en Render")
            return cls(selected, code, key, PRODUCTION_HOST, return_url)
        return cls(selected, INTEGRATION_CODE, INTEGRATION_API_KEY, INTEGRATION_HOST, return_url)


def validate_checkout_url(url, expected_host):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == expected_host
                and parsed.port in (None, 443) and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path == CHECKOUT_PATH)
    except ValueError:
        return False


def is_authorized(result, *, expected_order, expected_session, expected_amount):
    """Sólo compara datos contra un intento que DEBE estar persistido en servidor."""
    if not isinstance(result, dict) or type(expected_amount) is not int or expected_amount <= 0:
        return False
    try:
        amount = Decimal(str(result.get("amount")))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return (result.get("status") == "AUTHORIZED"
            and type(result.get("response_code")) is int
            and result["response_code"] == 0
            and result.get("buy_order") == expected_order
            and result.get("session_id") == expected_session
            and amount == Decimal(expected_amount))


class Gateway:
    def __init__(self, settings=None):
        self.config = settings if settings is not None else GatewaySettings.from_environment()
        self.opener = build_opener(NoRedirect())

    @property
    def endpoint(self):
        return f"https://{self.config.host}{API_PATH}"

    def _request(self, method, *, token=None, data=None):
        if self.config.environment == "disabled":
            raise GatewayError("Webpay no está habilitado")
        if token is not None and not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
            raise GatewayError("Identificador de transacción inválido")
        request = Request(
            self.endpoint + ("/" + token if token else ""),
            method=method,
            data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers={"Content-Type": "application/json",
                     "Tbk-Api-Key-Id": self.config.commerce_code,
                     "Tbk-Api-Key-Secret": self.config.api_key})
        try:
            with self.opener.open(request, timeout=15) as response:
                result = json.loads(response.read(100001).decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (HTTPError, URLError, OSError, TimeoutError, ValueError, UnicodeDecodeError):
            raise GatewayError("Transbank no respondió correctamente; verificar la operación antes de reintentar") from None

    def create_for_existing_hold(self, *, order, session_id, service):
        """Sólo el orquestador con bloqueo de hora PERSISTIDO puede usar esta función.

        Jamás recibe el precio desde el navegador.
        """
        if service not in AMOUNTS:
            raise GatewayError("Servicio inválido")
        if not isinstance(order, str) or not re.fullmatch(r"PSI[A-Z0-9]{8,22}", order):
            raise GatewayError("Orden inválida")
        if not isinstance(session_id, str) or not re.fullmatch(r"PSIS[A-Z0-9]{8,40}", session_id):
            raise GatewayError("Sesión inválida")
        result = self._request("POST", data={
            "buy_order": order, "session_id": session_id, "amount": AMOUNTS[service],
            "return_url": self.config.return_url})
        if (not isinstance(result.get("token"), str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", result["token"])
                or not validate_checkout_url(result.get("url"), self.config.host)):
            raise GatewayError("Respuesta de inicio de Transbank inválida")
        return {"token": result["token"], "url": result["url"]}

    def commit(self, token):
        """Sólo una vez por transacción, bajo control idempotente en base de datos."""
        return self._request("PUT", token=token, data={})

    def status(self, token):
        """Consultar estado para conciliar; NO confirma reservas por sí solo."""
        return self._request("GET", token=token)

