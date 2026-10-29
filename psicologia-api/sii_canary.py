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
from datetime import datetime

from flask import Blueprint, jsonify, request

import billing

bp=Blueprint("sii_canary",__name__,url_prefix="/admin")
SCHEMA="""CREATE TABLE IF NOT EXISTS sii_first_canary (
 id INTEGER PRIMARY KEY CHECK(id=1),
 order_id TEXT NOT NULL, nonce_hash TEXT NOT NULL UNIQUE,
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


def any_claim(con):
    con.executescript(SCHEMA)
    return bool(con.execute("SELECT 1 FROM sii_first_canary WHERE id=1").fetchone())


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
            order=con.execute("""SELECT environment,payment_status,invoice_status,folio,amount
                                 FROM billing_orders WHERE order_id=?""",(oid,)).fetchone()
            queued=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(oid,)).fetchone()
            if (not order or order["environment"]!="production"
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

