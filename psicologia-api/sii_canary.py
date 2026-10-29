"""One-off real SII issuance claim for the first supervised canary.

Does not click SII, create a folio, or automatically retry. A claim is persisted
*before* any irreversible SII action; failure requires manual reconciliation.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import secrets
import urllib.request
import urllib.error
from urllib.parse import urlparse, urlencode
from datetime import datetime

from flask import Blueprint, jsonify, request

import billing

bp=Blueprint("sii_canary",__name__,url_prefix="/admin")
SCHEMA="""CREATE TABLE IF NOT EXISTS sii_first_canary (
 id INTEGER PRIMARY KEY CHECK(id=1),
 order_id TEXT NOT NULL, nonce_hash TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('reconciliation','issued')),
 claimed_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS sii_issuance_attempts (
 order_id TEXT PRIMARY KEY REFERENCES billing_orders(order_id),
 nonce_hash TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('reconciliation','issued')),
 claimed_at TEXT NOT NULL);
"""


def ticket_data(token, *, claimed_completion=False):
    if not isinstance(token,str) or not 20<len(token)<=2048:
        return None
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    if len(secret)<32:
        return None
    try:
        a,b=token.split(".",1)
        expected=hmac.new(secret.encode(),a.encode("ascii"),hashlib.sha256).digest()
        received=base64.urlsafe_b64decode(b+"="*(-len(b)%4))
        raw=base64.urlsafe_b64decode(a+"="*(-len(a)%4))
        value=json.loads(raw)
        valid=(hmac.compare_digest(expected,received) and isinstance(value,dict)
               and value.get("mode")=="issue_canary"
               and type(value.get("exp")) is int
               and (int(time.time())-900<=value["exp"]<=int(time.time())+600
                    if claimed_completion else int(time.time())<=value["exp"]<=int(time.time())+600)
               and isinstance(value.get("nonce"),str)
               and len(value["nonce"])>=20
               and isinstance(value.get("preview_order"),str)
               and re.fullmatch(r"PSCP[0-9A-F]{18}",value["preview_order"]))
        return value if valid else None
    except Exception:
        return None


def any_claim(con, order_id=None):
    con.executescript(SCHEMA)
    if order_id is None:
        return bool(con.execute("SELECT 1 FROM sii_first_canary LIMIT 1").fetchone()
                    or con.execute("SELECT 1 FROM sii_issuance_attempts LIMIT 1").fetchone())
    return bool(con.execute("SELECT 1 FROM sii_first_canary WHERE order_id=?",(order_id,)).fetchone()
                or con.execute("SELECT 1 FROM sii_issuance_attempts WHERE order_id=?",
                               (order_id,)).fetchone())


def recorded_attempt(con, order_id):
    return (con.execute("SELECT order_id,nonce_hash,state FROM sii_issuance_attempts WHERE order_id=?",
                        (order_id,)).fetchone()
            or con.execute("SELECT order_id,nonce_hash,state FROM sii_first_canary WHERE order_id=?",
                           (order_id,)).fetchone())


@bp.post("/sii-visual/internal-canary-claim")
def claim():
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    supplied=request.headers.get("X-PSC-Visual-Bridge","")
    if (len(secret)<32 or not hmac.compare_digest(secret.encode(),supplied.encode())
        or os.environ.get("SII_CANARY_ENABLED")!="true"):
        return jsonify(error="no_autorizado"),403
    d=ticket_data((request.get_json(silent=True) or {}).get("ticket"))
    if not d:
        return jsonify(error="ticket_invalido"),403
    oid=d["preview_order"]
    try:
        with billing.db() as con:
            any_claim(con)
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM sii_first_canary WHERE id=1").fetchone():
                return jsonify(error="canary_ya_reclamado_revisar_sii"),409
            order=con.execute("""SELECT environment,payment_status,invoice_status,folio,amount,name
                                 FROM billing_orders WHERE order_id=?""",(oid,)).fetchone()
            queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(oid,)).fetchone()
            if (not order or "giovanni" not in order["name"].casefold()
                or "salas" not in order["name"].casefold()
                or order["environment"]!="production"
                or order["payment_status"]!="approved" or order["invoice_status"]!="pending"
                or order["folio"] or type(order["amount"]) is not int or order["amount"]<=0
                or not queued or queued["status"]!="awaiting_issuer"):
                return jsonify(error="orden_no_elegible"),409
            nonce_hash=hashlib.sha256(d["nonce"].encode()).hexdigest()
            con.execute("""INSERT INTO sii_first_canary
                           (id,order_id,nonce_hash,state,claimed_at)
                           VALUES (1,?,?,'reconciliation',?)""",
                        (oid,nonce_hash,billing.now()))
            con.execute("UPDATE sii_issuance_queue SET status='reconciliation',updated_at=? WHERE order_id=?",
                        (billing.now(),oid))
            con.execute("""INSERT INTO billing_audit(order_id,action,created_at,actor)
                           VALUES (?,'sii_canary_final_claimed',?,'sii_browser')""",(oid,billing.now()))
        return jsonify(claimed=True,order_id=oid)
    except sqlite3.IntegrityError:
        return jsonify(error="canary_ya_reclamado"),409

@bp.post("/sii-visual/internal-canary-complete")
def complete():
    """Persist official SII folio before attempting PDF or PSC email."""
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    supplied=request.headers.get("X-PSC-Visual-Bridge","")
    if len(secret)<32 or not hmac.compare_digest(secret.encode(),supplied.encode()):
        return jsonify(error="no_autorizado"),403
    if (request.content_length or 0)>7_100_000:
        return jsonify(error="documento_demasiado_grande"),413
    body=request.get_json(silent=True) or {}
    d=ticket_data(body.get("ticket"),claimed_completion=True)
    if not d:return jsonify(error="ticket_invalido"),403
    oid=d["preview_order"]
    with billing.db() as con:
        any_claim(con)
        record=con.execute("SELECT * FROM sii_first_canary WHERE id=1").fetchone()
        if (not record or record["order_id"]!=oid
            or record["nonce_hash"]!=hashlib.sha256(d["nonce"].encode()).hexdigest()):
            return jsonify(error="intento_no_reconocido"),409
        row=con.execute("SELECT amount FROM billing_orders WHERE order_id=?",(oid,)).fetchone()
        if not row:return jsonify(error="compra_no_encontrada"),404
        amount=row["amount"]
    snap=body.get("snapshot")
    if (not isinstance(snap,dict) or snap.get("dte")!=41
        or type(snap.get("amount")) is not int or snap["amount"]!=amount
        or not re.fullmatch(r"[1-9][0-9]{0,9}",str(snap.get("folio","")))):
        return jsonify(error="folio_tipo_o_monto_no_verificados"),409
    pdf=None
    encoded=snap.get("pdf")
    if isinstance(encoded,str) and len(encoded)<=7_000_000:
        if encoded.startswith("data:application/pdf;base64,"):
            encoded=encoded.split(",",1)[1]
        try:
            data=base64.b64decode(encoded,validate=True)
            from sii_post_issue import validate_pdf
            validate_pdf(data)
            pdf=data
        except (ValueError,binascii.Error):
            pdf=None
    from sii_post_issue import record_issued,store_pdf
    try:
        today=datetime.now(billing.TZ).date().isoformat()
        record_issued(oid,str(snap["folio"]),today,amount,dte=41)
        if pdf:
            store_pdf(oid,pdf)
    except (RuntimeError,ValueError,sqlite3.IntegrityError):
        return jsonify(error="registro_requiere_conciliacion"),409
    psc="pending"
    if pdf:
        try:
            from sii_receipt_admin import dispatch_psc
            psc=dispatch_psc(oid)
        except Exception:
            psc="unknown"
    return jsonify(state="issued",folio_recorded=True,pdf_stored=bool(pdf),
                   psc_delivery=psc)



@bp.post("/sii-visual/internal-sii-delivery")
def sii_delivery():
    """Signed one-shot portal-email claim and confirmation for an emitted receipt.

    A claim is persisted before touching the SII mail button. If Chromium
    crashes or SII's response is ambiguous, delivery is never retried
    automatically; 'sending'/'unknown' require manual reconciliation.
    """
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    supplied=request.headers.get("X-PSC-Visual-Bridge","")
    if len(secret)<32 or not hmac.compare_digest(secret.encode(),supplied.encode()):
        return jsonify(error="no_autorizado"),403
    payload=request.get_json(silent=True) or {}
    d=ticket_data(payload.get("ticket"),claimed_completion=True)
    if not d or payload.get("action") not in ("claim","complete"):
        return jsonify(error="ticket_invalido"),403
    order=d["preview_order"]
    from sii_post_issue import SCHEMA as receipts_schema
    with billing.db() as con:
        any_claim(con)
        con.executescript(receipts_schema)
        con.execute("BEGIN IMMEDIATE")
        issue=con.execute("SELECT * FROM sii_first_canary WHERE id=1").fetchone()
        receipt=con.execute("SELECT folio,pdf,sii_state,recipient FROM sii_receipts WHERE order_id=?",
                            (order,)).fetchone()
        if (not issue or issue["order_id"]!=order or issue["state"]!="issued"
            or issue["nonce_hash"]!=hashlib.sha256(d["nonce"].encode()).hexdigest()
            or not receipt or not receipt["pdf"]
            or str(payload.get("folio"))!=receipt["folio"]):
            return jsonify(error="documento_no_confirmado"),409
        if payload["action"]=="claim":
            if receipt["sii_state"]!="pending":
                return jsonify(state=receipt["sii_state"],claimed=False),409
            con.execute("UPDATE sii_receipts SET sii_state='sending',updated_at=? WHERE order_id=?",
                        (billing.now(),order))
            con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                        (order,"sii_email_claimed",billing.now(),"sii_browser"))
            return jsonify(state="sending",claimed=True,recipient=receipt["recipient"])
        if receipt["sii_state"]!="sending":
            return jsonify(error="envio_ya_resuelto"),409
        outcome=payload.get("outcome")
        if outcome not in ("accepted","unknown"):
            return jsonify(error="estado_invalido"),400
        reference="sii-portal-"+receipt["folio"] if outcome=="accepted" else None
        con.execute("""UPDATE sii_receipts SET sii_state=?,sii_reference=?,updated_at=?
                       WHERE order_id=?""",(outcome,reference,billing.now(),order))
        con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                    (order,"sii_email_"+outcome,billing.now(),"sii_browser"))
        return jsonify(state=outcome)





def _visual_service():
    base=os.environ.get("SII_VISUAL_BROWSER_URL","").strip().rstrip("/")
    parsed=urlparse(base)
    if parsed.scheme!="https" or not (parsed.hostname or "").endswith(".onrender.com") or parsed.username or parsed.password or parsed.port:
        raise RuntimeError("sii_browser_not_configured")
    secret=os.environ.get("SII_VISUAL_TICKET_SECRET","")
    if len(secret)<32:
        raise RuntimeError("sii_bridge_secret_missing")
    return base,secret


def _server_request(url,secret,body=None):
    headers={"X-PSC-Visual-Bridge":secret, "Accept":"application/json"}
    if body is None:
        req=urllib.request.Request(url,headers=headers,method="GET")
    else:
        headers["Content-Type"]="application/json"
        req=urllib.request.Request(url,data=json.dumps(body,separators=(",",":")).encode(),
                                   headers=headers,method="POST")
    with urllib.request.urlopen(req,timeout=22) as response:
        return response.status,json.loads(response.read(4096))



@bp.post("/boletas/<order>/prepare-render")
@billing.private
def prepare_render(order):
    """Run a silent read-only SII preparation, never issue a document."""
    if not re.fullmatch(r"PSCP[0-9A-F]{18}",order):
        return jsonify(error="Compra inválida."),400
    try:
        base,secret=_visual_service()
        with billing.db() as con:
            row=con.execute("""SELECT environment,payment_status,invoice_status,folio
                               FROM billing_orders WHERE order_id=?""",(order,)).fetchone()
            if (not row or row["environment"]!="production"
                or row["payment_status"]!="approved"
                or row["invoice_status"]!="pending" or row["folio"]):
                return jsonify(error="Compra no elegible para preparar una boleta."),409
            queue=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order,)).fetchone()
            if queue and queue["status"] in ("processing","reconciliation"):
                return jsonify(error="Primero hay que conciliar la emisión anterior; no iniciar otra sesión."),409
        from sii_visual_admin import _b64
        payload=_b64(json.dumps({"exp":int(time.time())+600,
            "nonce":secrets.token_urlsafe(24),"preview_order":order,
            "mode":"preview"},separators=(",",":")).encode())
        signature=_b64(hmac.new(secret.encode(),payload.encode("ascii"),hashlib.sha256).digest())
        status,_data=_server_request(base+"/api/internal/start",secret,
                                     {"ticket":payload+"."+signature})
        if status!=202:
            return jsonify(error="Chromium no confirmó el inicio de la preparación."),503
        with billing.db() as con:
            con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                        (order,"render_sii_preview_started",billing.now(),request.authorization.username))
        return jsonify(started=True,state="starting",order_id=order,issuance=False),202
    except urllib.error.HTTPError as exc:
        if exc.code==409:
            return jsonify(error="Hay otra operación SII todavía activa. Solo se liberan automáticamente las vistas previas finalizadas; no se emitió una boleta."),409
        if exc.code in (401,403):
            return jsonify(error="Render rechazó la autorización privada de la preparación. No se emitió una boleta."),503
        return jsonify(error="El navegador de Render no pudo cargar la compra desde PSC. Revisa la conexión SII; no se emitió una boleta."),503
    except (RuntimeError,ValueError,urllib.error.URLError,TimeoutError,OSError):
        return jsonify(error="No se pudo confirmar el inicio de Chromium. Revisa el estado antes de reintentar."),503


@bp.get("/boletas/<order>/prepare-status")
@billing.private
def prepare_status(order):
    if not re.fullmatch(r"PSCP[0-9A-F]{18}",order):
        return jsonify(error="Compra inválida."),400
    with billing.db() as con:
        row=con.execute("SELECT environment,payment_status,invoice_status,folio FROM billing_orders WHERE order_id=?",(order,)).fetchone()
    if not row or row["environment"]!="production" or row["payment_status"]!="approved":
        return jsonify(error="Compra no encontrada."),404
    if row["invoice_status"]=="issued" or row["folio"]:
        return jsonify(state="issued",stage="issued")
    try:
        base,secret=_visual_service()
        _,result=_server_request(base+"/api/internal/status?"+urlencode({"order_id":order}),secret)
        return jsonify(state=result.get("state","unknown"),
                       stage=result.get("stage","unknown"),
                       preparation=result.get("preparation"))
    except Exception:
        return jsonify(state="unavailable",stage="render_unavailable")


@bp.post("/boletas/<order>/emit")
@billing.private
def emit_from_panel(order):
    """One administrator click -> Render Chromium -> SII, never local/manual."""
    if os.environ.get("SII_CANARY_ENABLED")!="true":
        return jsonify(error="La emisión desde Render todavía está deshabilitada."),503
    if not re.fullmatch(r"PSCP[0-9A-F]{18}",order):
        return jsonify(error="Compra inválida."),400
    try:
        base,secret=_visual_service()
        with billing.db() as con:
            if any_claim(con):
                return jsonify(error="Ya existe un intento real. Primero revisa el resultado SII."),409
            row=con.execute("""SELECT name,environment,payment_status,invoice_status,folio
                               FROM billing_orders WHERE order_id=?""",(order,)).fetchone()
            if (not row or row["environment"]!="production" or row["payment_status"]!="approved"
                or row["invoice_status"]!="pending" or row["folio"]):
                return jsonify(error="La compra no tiene una boleta pendiente válida."),409
            if "giovanni" not in row["name"].casefold() or "salas" not in row["name"].casefold():
                return jsonify(error="La primera emisión desde Render está reservada para Giovanni Salas."),409
            queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order,)).fetchone()
            if queued is None:
                billing._queue_sii_after_approved(con,order)
            elif queued["status"]!="awaiting_issuer":
                return jsonify(error="Emisión iniciada o pendiente de conciliación. No repetir."),409
        from sii_visual_admin import _b64
        payload=_b64(json.dumps({"exp":int(time.time())+600,
               "nonce":secrets.token_urlsafe(24),"preview_order":order,
               "mode":"issue_canary"},separators=(",",":")).encode())
        signature=_b64(hmac.new(secret.encode(),payload.encode("ascii"),hashlib.sha256).digest())
        ticket=payload+"."+signature
        status,data=_server_request(base+"/api/internal/start",secret,{"ticket":ticket})
        if status!=202:
            return jsonify(error="Chromium no confirmó el inicio."),503
        with billing.db() as con:
            con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                        (order,"render_sii_emission_started",billing.now(),request.authorization.username))
        return jsonify(started=True,state="starting",order_id=order),202
    except urllib.error.HTTPError as exc:
        return jsonify(error="Chromium rechazó el inicio; revisa su estado antes de repetir."),409 if exc.code==409 else 503
    except (RuntimeError,ValueError,urllib.error.URLError,TimeoutError,OSError):
        return jsonify(error="No se pudo confirmar el inicio en Render. Comprueba el estado antes de intentar otra vez."),503


@bp.get("/boletas/<order>/issue-status")
@billing.private
def issue_status(order):
    with billing.db() as con:
        queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order,)).fetchone()
        receipt=con.execute("SELECT invoice_status,folio FROM billing_orders WHERE order_id=?",(order,)).fetchone()
    if not receipt:
        return jsonify(error="Compra no encontrada."),404
    if receipt["invoice_status"]=="issued":
        return jsonify(state="issued",folio=receipt["folio"])
    try:
        base,secret=_visual_service()
        _,status=_server_request(base+"/api/internal/status?"+urlencode({"order_id":order}),secret)
        current=status.get("state","unknown")
        stage=status.get("stage","unknown")
        if queued and queued["status"]=="reconciliation":
            # Claim is persisted BEFORE the irreversible SII click.
            # It means "do not retry", NOT "Chromium finished".
            if current in ("starting","open") and not stage.startswith("canary_"):
                return jsonify(state="running",stage=stage,claimed=True)
            return jsonify(state="reconciliation",stage=stage,
                           message="Resultado de emisión pendiente de verificación en el SII. No reintentar.")
        return jsonify(state=current,stage=stage,preparation=status.get("preparation"))
    except Exception:
        if queued and queued["status"]=="reconciliation":
            return jsonify(state="reconciliation",stage="render_unavailable",
                           message="Verificar folio en SII antes de volver a intentar.")
        return jsonify(state="unavailable",stage="render_unavailable")
