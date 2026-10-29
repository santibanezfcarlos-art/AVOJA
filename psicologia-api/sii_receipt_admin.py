"""Admin-only official receipt completion: upload PDF, download, PSC dispatch.

A receipt is never issued by these routes. They only accept actual, already
issued folios tied to approved production purchases.
"""
import io
import os
import re

from flask import Blueprint, jsonify, request, send_file

import billing
import sii_post_issue as ledger

bp = Blueprint("sii_receipt_admin", __name__)
MAX_PDF = 5_000_000


def _receipts(con):
    con.executescript(ledger.SCHEMA)


@bp.get("/admin/boletas/receipts")
@billing.private
def statuses():
    with billing.db() as con:
        _receipts(con)
        rows=con.execute("""SELECT order_id,folio,pdf IS NOT NULL AS has_pdf,
                             sii_state,psc_state
                             FROM sii_receipts ORDER BY updated_at DESC LIMIT 1000""").fetchall()
        from sii_canary import any_claim
        claimed=any_claim(con)
        targets=con.execute("""SELECT order_id FROM billing_orders
                               WHERE environment='production' AND payment_status='approved'
                               AND invoice_status='pending' AND (folio IS NULL OR folio='')
                               AND instr(lower(name),'giovanni')>0
                               AND instr(lower(name),'salas')>0""").fetchall()
        selected=targets[0]['order_id'] if len(targets)==1 else None
    return jsonify(receipts=[dict(r) for r in rows], post_issue_enabled=True,
                   first_canary_ready=os.environ.get("SII_CANARY_ENABLED")=="true" and not claimed and bool(selected),
                   first_canary_order_id=selected if not claimed else None,
                   first_canary_claimed=claimed)


@bp.post("/admin/boletas/<order>/official-pdf")
@billing.private
def store_official_pdf(order):
    if (request.content_length or 0) > MAX_PDF+5000:
        return jsonify(error="Archivo demasiado grande"),413
    upload=request.files.get("file")
    if not upload:
        return jsonify(error="Adjunta el PDF emitido en el SII"),400
    pdf=upload.stream.read(MAX_PDF+1)
    if len(pdf)>MAX_PDF:
        return jsonify(error="PDF demasiado grande"),413
    try:
        with billing.db() as con:
            row=con.execute("SELECT * FROM billing_orders WHERE order_id=?",(order,)).fetchone()
        if not row:return jsonify(error="Compra no encontrada"),404
        if (row["environment"]!="production" or row["payment_status"]!="approved"
            or row["invoice_status"]!="issued" or not row["folio"] or not row["issued_date"]):
            return jsonify(error="Primero emite la boleta real en el SII y registra su folio"),409
        ledger.record_issued(order,str(row["folio"]),row["issued_date"],row["amount"])
        ledger.store_pdf(order,pdf)
        return jsonify(ok=True,folio=row["folio"],pdf_stored=True)
    except (ValueError,RuntimeError) as exc:
        return jsonify(error=str(exc)),409


@bp.get("/admin/boletas/<order>/official-pdf")
@billing.private
def download_official_pdf(order):
    with billing.db() as con:
        _receipts(con)
        row=con.execute("SELECT folio,pdf FROM sii_receipts WHERE order_id=?",(order,)).fetchone()
    if not row or not row["pdf"]:
        return jsonify(error="Aún no hay un PDF oficial guardado"),404
    return send_file(io.BytesIO(row["pdf"]),mimetype="application/pdf",
                     as_attachment=True,download_name="boleta-"+row["folio"]+".pdf")


def dispatch_psc(order):
    """Single external send through the existing Resend production transport.

    On uncertainty the ledger remains unknown: never silently resend.
    """
    from production_checkout import remote
    def sender(receipt,key):
        result=remote("receipt-mail",{"order":order,"folio":receipt["folio"],
                                      "mail":ledger.mail_payload(receipt)})
        if result.get("state")!="SENT" or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}",str(result.get("reference",""))):
            raise RuntimeError("delivery_not_confirmed")
        return result["reference"]
    return ledger.deliver(order,"psc",sender)


@bp.post("/admin/boletas/<order>/send-psc")
@billing.private
def mail_official_pdf(order):
    try:
        state=dispatch_psc(order)
        return jsonify(state=state,accepted_by_provider=state=="accepted")
    except (RuntimeError,ValueError) as exc:
        return jsonify(error=str(exc)),409
