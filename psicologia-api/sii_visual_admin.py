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

from flask import Blueprint, jsonify, send_file
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
    return jsonify(
        url=base + "/#ticket=" + ticket,
        expires_in_seconds=600,
        message="Enlace de un solo uso; no incluye credenciales del SII.",
    )


def request_actor():
    from flask import request
    return request.authorization.username if request.authorization else "admin"
