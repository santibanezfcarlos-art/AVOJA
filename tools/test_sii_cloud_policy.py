"""Safety tests for the cloud DTE 41 eligibility gate. Run: python -m unittest discover -s tools -p 'test_sii_cloud_policy.py'"""
import unittest
from sii_cloud_policy import IssuanceEligibility, check_eligibility


class IssuancePolicyTests(unittest.TestCase):
    def purchase(self, **changes):
        payload = dict(order_id="order-1", source="webpay",
                       payment_confirmed_server_side=True, test_purchase=False,
                       amount_clp=30000, document_type=41,
                       already_has_folio=False, has_attempt_marker=False,
                       operator_authorized=True)
        payload.update(changes)
        return IssuanceEligibility(**payload)

    def test_candidate_is_not_emission(self):
        self.assertEqual(check_eligibility(self.purchase()), (True, "eligible_for_further_validation"))

    def test_test_purchase_blocked_even_if_paid(self):
        self.assertEqual(check_eligibility(self.purchase(test_purchase=True))[1], "test_purchase")

    def test_unverified_payment_blocked(self):
        self.assertEqual(check_eligibility(self.purchase(payment_confirmed_server_side=False))[1], "unconfirmed_payment")

    def test_duplicate_attempt_blocked(self):
        self.assertEqual(check_eligibility(self.purchase(has_attempt_marker=True))[1], "reconciliation_required")

    def test_existing_folio_blocked(self):
        self.assertEqual(check_eligibility(self.purchase(already_has_folio=True))[1], "already_issued")

    def test_wrong_document_type_blocked(self):
        self.assertEqual(check_eligibility(self.purchase(document_type=39))[1], "wrong_document_type")

    def test_operator_required(self):
        self.assertEqual(check_eligibility(self.purchase(operator_authorized=False))[1], "operator_authorization_required")

    def test_zero_and_boolean_amount_blocked(self):
        self.assertEqual(check_eligibility(self.purchase(amount_clp=0))[1], "invalid_amount")
        self.assertEqual(check_eligibility(self.purchase(amount_clp=True))[1], "invalid_amount")


if __name__ == "__main__":
    unittest.main()
