"""Admin-only launcher for a short-lived SII visual browser session."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, redirect, request, send_file
import billing

bp = Blueprint("sii_visual_admin", __name__, url_prefix="/admin")


@bp.get('/sii-visual/post-emission/email')
@billing.private
def post_emission_email_preview():
    from sii_post_issue import email_content
    with billing.db() as con:
        row = con.execute("SELECT name,amount,detail FROM billing_orders WHERE environment='production' "
                          "AND payment_status='approved' AND invoice_status='pending' "
                          "ORDER BY COALESCE(paid_at,created_at),created_at,order_id LIMIT 1").fetchone()
    receipt = {'patient_name':row['name'] if row else 'Cliente de ejemplo',
               'amount':row['amount'] if row else 30000,
               'detail':row['detail'] if row else 'Servicio de ejemplo'}
    _, body, _ = email_content(receipt,preview=True)
    return Response(body,mimetype='text/html',headers={'Cache-Control':'no-store, private',
        'Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"})


@bp.get('/sii-visual/post-emission')
@billing.private
def post_emission_preview():
    response = send_file(Path(__file__).with_name('sii_post_issue_preview.html'))
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


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

    # The canary is manually approved in the private visual browser.
    mode=(request.form.get("mode") or "preview").strip()
    if mode not in ("preview","issue_canary"):
        return jsonify(error="Acción SII inválida."),400
    if mode=="issue_canary" and os.environ.get("SII_CANARY_ENABLED")!="true":
        return jsonify(error="Emisión real supervisada aún no habilitada."),503

    # Bind the signed SII session to the EXACT paid order chosen in the panel.
    # An omitted ID retains the old 'first pending' diagnostic behavior.
    selected_order = (request.form.get("order_id") or "").strip()
    if selected_order and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", selected_order):
        return jsonify(error="Identificador de compra inválido."), 400
    with billing.db() as con:
        sql = ("SELECT * FROM billing_orders WHERE environment='production' "
               "AND payment_status='approved' AND invoice_status='pending' "
               "AND (folio IS NULL OR folio='')")
        if selected_order:
            sql += " AND order_id=?"
        sql += " ORDER BY COALESCE(paid_at,created_at),created_at,order_id LIMIT 1"
        row = con.execute(sql, (selected_order,) if selected_order else ()).fetchone()
        if row is None:
            return jsonify(error="No hay compras reales pagadas pendientes de boleta."), 409
        order_id = row["order_id"]
        if mode=="issue_canary":
            from sii_canary import any_claim
            if any_claim(con,order_id):
                return jsonify(error="Este pago ya tiene un intento real. Verifica su folio en el SII."),409
            queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order_id,)).fetchone()
            if queued is None:
                # Giovanni and other legitimate pre-integration purchases may
                # predate the issue-request queue. Bind only the selected order.
                billing._queue_sii_after_approved(con,order_id)
                queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order_id,)).fetchone()
            if not queued or queued["status"]!="awaiting_issuer":
                return jsonify(error="La compra no está disponible para la primera emisión."),409
    now = int(time.time())
    payload = _b64(json.dumps(
        {"exp": now + 600, "nonce": secrets.token_urlsafe(24), "preview_order": order_id, "mode":mode},
        separators=(",", ":"),
    ).encode("utf-8"))
    signature = _b64(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    ticket = payload + "." + signature
    try:
        with billing.db() as con:
            con.execute(
                "INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                (order_id, "visual_session_created", billing.now(),
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
    order_id = data.get("preview_order")
    if not isinstance(order_id, str):
        return jsonify(error="Abre una nueva vista previa desde el panel PSC."), 409
    with billing.db() as con:
        row = con.execute("SELECT * FROM billing_orders WHERE order_id=?", (order_id,)).fetchone()
        if (not row or row["environment"] != "production" or row["payment_status"] != "approved"
                or row["invoice_status"] != "pending" or row["folio"]
                or type(row["amount"]) is not int or row["amount"] <= 0):
            return jsonify(error="La compra ya no está pendiente o no es elegible."), 409
        try:
            person = billing.receiver({"billing_" + k: row[k] for k in ("rut", "name", "address", "email", "phone")})
            if not 1 <= len(row["detail"].strip()) <= 80:
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify(error="Los datos de la compra están incompletos."), 409
        preview_order = {"order_id": order_id, "amount": row["amount"],
                         "rut": person["rut"], "nombre": person["name"],
                         "direccion": person["address"], "email": person["email"],
                         "telefono": person["phone"], "detalle": row["detail"]}
        con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                    (order_id, "visual_preview_requested_no_issuance", billing.now(), "sii-visual"))
    response = jsonify(rut=rut, password=password, preview_order=preview_order)
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return response


def request_actor():
    from flask import request
    return request.authorization.username if request.authorization else "admin"

