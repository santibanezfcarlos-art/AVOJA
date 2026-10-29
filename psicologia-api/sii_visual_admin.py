"""Admin-only launcher for a short-lived SII visual browser session."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, jsonify, redirect, request, send_file
import billing

bp = Blueprint("sii_visual_admin", __name__, url_prefix="/admin")


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@bp.get("/sii-visual")
@billing.private
def sii_visual_page():
    return send_file(Path(__file__).with_name("sii_visual_admin.html"))


@bp.post("/sii-visual/session")
@billing.private
def create_sii_visual_session():
    base = os.environ.get("SII_VISUAL_BROWSER_URL", "").strip().rstrip("/")
    secret = os.environ.get("SII_VISUAL_TICKET_SECRET", "")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".onrender.com"):
        return jsonify(error="El servicio visual SII no está configurado."), 503
    if len(secret) < 32:
        return jsonify(error="Falta la clave de autorización temporal del servicio visual."), 503

    now = int(time.time())
    payload = _b64(json.dumps(
        {"exp": now + 600, "nonce": secrets.token_urlsafe(24)},
        separators=(",", ":"),
    ).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    ticket = payload + "." + signature
    try:
        with billing.db() as con:
            con.execute(
                "INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                ("sii_visual", "visual_session_created", billing.now(),
                 request_actor()),
            )
    except Exception:
        # A short-lived bearer ticket is still safe to issue if the audit store is
        # temporarily unavailable; never log or persist the ticket itself.
        pass
    response = redirect(base + "/#ticket=" + ticket, code=303)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@bp.post("/sii-visual/internal-credentials")
def sii_visual_internal_credentials():
    """Server-only bridge for the visual browser; never used by client JavaScript."""
    bridge_secret = os.environ.get("SII_VISUAL_TICKET_SECRET", "")
    supplied = request.headers.get("X-PSC-Visual-Bridge", "")
    if len(bridge_secret) < 32 or not hmac.compare_digest(
        supplied.encode("utf-8"), bridge_secret.encode("utf-8")
    ):
        return jsonify(error="Solicitud no autorizada"), 401

    body = request.get_json(silent=True) or {}
    ticket = body.get("ticket", "")
    signing_secret = os.environ.get("SII_VISUAL_TICKET_SECRET", "")
    if not isinstance(ticket, str) or len(ticket) > 2048 or len(signing_secret) < 32:
        return jsonify(error="Enlace de sesión inválido"), 403
    try:
        payload, signature = ticket.split(".", 1)
        expected = hmac.new(signing_secret.encode(), payload.encode("ascii"), hashlib.sha256).digest()
        received = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(raw)
        now = int(time.time())
        if (
            not hmac.compare_digest(expected, received)
            or not isinstance(data, dict)
            or not isinstance(data.get("nonce"), str)
            or type(data.get("exp")) is not int
            or not now <= data["exp"] <= now + 600
        ):
            return jsonify(error="Enlace de sesión inválido"), 403
    except Exception:
        return jsonify(error="Enlace de sesión inválido"), 403

    rut = os.environ.get("SII_RUT", "")
    password = os.environ.get("SII_PASSWORD", "")
    if not rut or not password:
        return jsonify(error="Credenciales SII no configuradas"), 503
    response = jsonify(rut=rut, password=password)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return response


def request_actor():
    from flask import request
    return request.authorization.username if request.authorization else "admin"
