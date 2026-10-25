"""PSC Santibanez - policy gate for future SII DTE 41 cloud issuance.

This module intentionally contains NO SII credentials, network calls, signing,
CAF consumption or production issuance. It may be imported safely by a
future certified cloud DTE backend; it cannot issue documents on its own.
"""
from dataclasses import dataclass
from enum import Enum


class IssuanceState(str, Enum):
    READY = "ready"
    PROCESSING = "processing"
    RECONCILIATION = "reconciliation"
    ISSUED = "issued"
    REJECTED = "rejected"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class IssuanceEligibility:
    order_id: str
    source: str  # "webpay" or a separately approved real-payment source
    payment_confirmed_server_side: bool
    test_purchase: bool
    amount_clp: int
    document_type: int
    already_has_folio: bool
    has_attempt_marker: bool
    operator_authorized: bool


def check_eligibility(record: IssuanceEligibility) -> tuple[bool, str]:
    """Fail-closed eligibility check; not permission to submit to SII.

    Production must ALSO enforce transactional idempotency in durable storage,
    verify authenticated operator, validate issuer authorization/certification,
    CAF and digital signature, and reconcile uncertain submissions with SII.
    """
    if not isinstance(record.order_id, str) or not record.order_id.strip():
        return False, "missing_order"
    if record.test_purchase:
        return False, "test_purchase"
    if record.document_type != 41:
        return False, "wrong_document_type"
    if record.source != "webpay" or not record.payment_confirmed_server_side:
        return False, "unconfirmed_payment"
    if type(record.amount_clp) is not int or record.amount_clp <= 0:
        return False, "invalid_amount"
    if record.already_has_folio:
        return False, "already_issued"
    if record.has_attempt_marker:
        return False, "reconciliation_required"
    if not record.operator_authorized:
        return False, "operator_authorization_required"
    return True, "eligible_for_further_validation"
