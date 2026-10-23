"""Webpay Plus integration adapter, not enabled by default.

Only the sandbox environment is supported. No reservation is confirmed by this module:
the booking orchestrator must persist a hold and reconcile an AUTHORIZED commit.
"""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

INTEGRATION_BASE = "https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions"
TEST_COMMERCE_CODE = "597055555532"
ALLOWED_AMOUNTS = {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000}
DURATIONS = {"psicoterapia": 45, "empleabilidad": 90, "competencias": 45}


class WebpayNotReady(RuntimeError):
    pass


def integration_enabled():
    return os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") == "integration"


def _headers():
    if not integration_enabled():
        raise WebpayNotReady("Webpay integration is disabled")
    commerce_code = os.environ.get("PSICOLOGIA_WEBPAY_COMMERCE_CODE", "")
    api_key = os.environ.get("PSICOLOGIA_WEBPAY_API_KEY", "")
    if commerce_code != TEST_COMMERCE_CODE or not api_key:
        raise WebpayNotReady("Only the official test commerce is accepted")
    return {"Content-Type": "application/json", "Tbk-Api-Key-Id": commerce_code, "Tbk-Api-Key-Secret": api_key}


def service_amount(service):
    if service not in ALLOWED_AMOUNTS:
        raise ValueError("Invalid service")
    return ALLOWED_AMOUNTS[service]


def _call(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=_headers(), method=method)
    try:
        with urlopen(req, timeout=12) as response:
            return json.loads(response.read(16384).decode("utf-8"))
    except (HTTPError, URLError, ValueError) as exc:
        raise WebpayNotReady("Could not complete the test transaction") from exc


def create_test_transaction(service, buy_order, session_id, return_url):
    if not isinstance(buy_order, str) or not (1 <= len(buy_order) <= 26):
        raise ValueError("Invalid buy order")
    if not isinstance(session_id, str) or not (1 <= len(session_id) <= 61):
        raise ValueError("Invalid session")
    if not isinstance(return_url, str) or not return_url.startswith("https://psicologia-carlos-calendar-api.onrender.com/"):
        raise ValueError("Return URL must belong to the application backend")
    # The amount always comes from the server, never from form fields.
    payload = {"buy_order": buy_order, "session_id": session_id, "amount": service_amount(service), "return_url": return_url}
    result = _call("POST", INTEGRATION_BASE, payload)
    if result.get("url") != "https://webpay3gint.transbank.cl/webpayserver/initTransaction" or not result.get("token"):
        raise WebpayNotReady("Unexpected response from Webpay")
    return result


def commit_test_transaction(token):
    if not isinstance(token, str) or not (12 <= len(token) <= 128) or not token.isalnum():
        raise ValueError("Invalid Webpay token")
    result = _call("PUT", INTEGRATION_BASE + "/" + token)
    # An authorized response must ALSO be matched to a persisted hold before reserving.
    return result


def is_authorized(result, service, buy_order):
    return (result.get("status") == "AUTHORIZED"
            and result.get("response_code") == 0
            and result.get("amount") == service_amount(service)
            and result.get("buy_order") == buy_order)
