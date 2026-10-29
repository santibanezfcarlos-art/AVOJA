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
RESEND_SCHEMA = """CREATE TABLE IF NOT EXISTS sii_receipt_resends (
 order_id TEXT NOT NULL, sequence INTEGER NOT NULL, state TEXT NOT NULL,
 reference TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 PRIMARY KEY(order_id,sequence));
"""



def _receipts(con):
    con.executescript(ledger.SCHEMA)


@bp.get("/admin/boletas/receipts")
@billing.private
def statuses():
    with billing.db() as con:
        _receipts(con)
        con.executescript(RESEND_SCHEMA)
        rows=con.execute("""SELECT r.order_id,r.folio,r.pdf IS NOT NULL AS has_pdf,
                             r.sii_state,r.psc_state,
                             COALESCE((SELECT MAX(sequence) FROM sii_receipt_resends
                                        WHERE order_id=r.order_id),0) AS resend_count,
                             (SELECT state FROM sii_receipt_resends
                              WHERE order_id=r.order_id ORDER BY sequence DESC LIMIT 1)
                               AS resend_state
                             FROM sii_receipts r ORDER BY r.updated_at DESC LIMIT 1000""").fetchall()
    return jsonify(receipts=[dict(r) for r in rows], post_issue_enabled=True,
                   issuance_enabled=os.environ.get("SII_CANARY_ENABLED")=="true")


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


@bp.post("/admin/boletas/<order>/resend-psc")
@billing.private
def resend_psc_receipt(order):
    """Explicit admin-initiated *new* email, never an automatic retry.

    Persist send intent and sequence before any external side effect.
    Once a resend is uncertain it cannot be retried blindly.
    """
    from production_checkout import remote
    if not re.fullmatch(r"PSCP[0-9A-F]{18}",order):
        return jsonify(error="Compra inválida"),400
    with billing.db() as con:
        _receipts(con)
        con.executescript(RESEND_SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        original=con.execute("SELECT * FROM sii_receipts WHERE order_id=?",(order,)).fetchone()
        if (not original or not original["pdf"] or original["psc_state"]!="accepted"):
            return jsonify(error="Primero confirma el envío original y el PDF de esta boleta."),409
        latest=con.execute("""SELECT sequence,state FROM sii_receipt_resends
                              WHERE order_id=? ORDER BY sequence DESC LIMIT 1""",
                           (order,)).fetchone()
        if latest and latest["state"] in ("sending","unknown"):
            return jsonify(error="Un reenvío anterior requiere conciliación; no se enviará otra copia."),409
        seq=(latest["sequence"] if latest else 0)+1
        if seq>100:return jsonify(error="Límite de reenvíos alcanzado."),409
        con.execute("""INSERT INTO sii_receipt_resends
            (order_id,sequence,state,created_at,updated_at)
            VALUES (?,?,'sending',?,?)""",(order,seq,billing.now(),billing.now()))
        payload={"order":order,"folio":original["folio"],
                 "resend_attempt":seq,
                 "mail":ledger.mail_payload(dict(original))}
    state="unknown"
    reference=None
    try:
        result=remote("receipt-mail",payload)
        if (result.get("state")=="SENT"
            and isinstance(result.get("reference"),str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,200}",result["reference"])):
            state="accepted"
            reference=result["reference"]
    except Exception:
        pass
    with billing.db() as con:
        con.execute("""UPDATE sii_receipt_resends
                       SET state=?,reference=?,updated_at=?
                       WHERE order_id=? AND sequence=? AND state='sending'""",
                    (state,reference,billing.now(),order,seq))
        con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                    (order,"psc_receipt_resend_"+state,billing.now(),"admin"))
    return jsonify(state=state,attempt=seq,accepted_by_provider=state=="accepted")
