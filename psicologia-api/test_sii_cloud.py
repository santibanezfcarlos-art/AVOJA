"""Offline checks for cloud SII readiness. Run from psicologia-api with pytest."""
from sii_cloud import assess


def sample(**overrides):
    row = dict(environment="production", payment_status="approved",
               invoice_status="pending", folio=None, amount=30000)
    row.update(overrides)
    return row


def test_real_order_still_does_not_issue():
    assert assess(sample()) == "eligible_for_future_validation"


def test_simulated_purchase_is_blocked():
    assert assess(sample(environment="integration")) == "test_purchase"


def test_payment_not_confirmed_is_blocked():
    assert assess(sample(payment_status="initiated")) == "payment_not_approved"


def test_issued_and_existing_folio_blocked():
    assert assess(sample(invoice_status="issued")) == "already_issued"
    assert assess(sample(folio="123")) == "already_issued"


def test_existing_attempt_requires_reconciliation():
    assert assess(sample(), existing_attempt=True) == "reconciliation_required"


def test_invalid_amount_blocked():
    assert assess(sample(amount=0)) == "invalid_amount"
    assert assess(sample(amount=True)) == "invalid_amount"
