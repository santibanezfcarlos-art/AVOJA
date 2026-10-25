"""Cloud DTE 41 readiness endpoint. No issuance, certificate or SII credentials.

A future SII-certified backend may build on this module. Current endpoint is
read-only and available solely to authenticated PSC administrators.
"""
from flask import Blueprint, jsonify
import billing

bp = Blueprint("sii_cloud", __name__)


def assess(row, existing_attempt=False):
    """Fail closed; never treat this result as permission to issue a real DTE."""
    if row is None:
        return "order_missing"
    if row["environment"] != "production":
        return "test_purchase"
    if row["payment_status"] != "approved":
        return "payment_not_approved"
    if row["invoice_status"] == "issued" or row["folio"]:
        return "already_issued"
    if row["invoice_status"] != "pending":
        return "invoice_not_pending"
    if type(row["amount"]) is not int or row["amount"] <= 0:
        return "invalid_amount"
    if existing_attempt:
        return "reconciliation_required"
    return "eligible_for_future_validation"


@bp.get("/admin/boletas/<order>/cloud-readiness")
@billing.private
def readiness(order):
    """A server-side diagnostic; purposely does not enqueue or emit anything."""
    import re
    if not re.fullmatch(r"PSCD[0-9A-F]{18}", order):
        return jsonify(error="Orden inválida"), 400
    with billing.db() as con:
        row = con.execute("SELECT order_id,environment,payment_status,invoice_status,folio,amount "
                          "FROM billing_orders WHERE order_id=?", (order,)).fetchone()
        # Any previous server/local work must be reconciled first.
        attempt = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sii_jobs'").fetchone()
        attempted = bool(attempt and con.execute(
            "SELECT 1 FROM sii_jobs WHERE order_id=? AND status IN "
            "('queued','running','reconciliation') LIMIT 1", (order,)).fetchone())
    if row is None:
        return jsonify(error="Compra no encontrada"), 404
    reason = assess(row, attempted)
    return jsonify(order_id=order, document_type=41, eligibility=reason,
                   cloud_issuance_enabled=False, emitted=False,
                   requirements=["sii_own_software_authorization",
                                 "digital_certificate_and_key_management",
                                 "caf_and_xml_signature",
                                 "sii_submission_and_acknowledgment",
                                 "durable_idempotency_and_reconciliation",
                                 "document_storage_and_delivery"])
