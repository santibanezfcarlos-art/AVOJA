import unittest
from unittest.mock import patch
from webpay_integration import (
    ALLOWED_AMOUNTS, DURATIONS, WebpayNotReady,
    create_test_transaction, is_authorized, service_amount,
)

class TestWebpayIntegrationSafety(unittest.TestCase):
    def test_service_amounts_and_durations(self):
        self.assertEqual(ALLOWED_AMOUNTS, {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000})
        self.assertEqual(DURATIONS, {"psicoterapia": 45, "empleabilidad": 90, "competencias": 45})
        with self.assertRaises(ValueError):
            service_amount("manual_price")

    def test_rejects_unexpected_return_url(self):
        with self.assertRaises(ValueError):
            create_test_transaction("psicoterapia", "TEST-100", "session-1", "https://evil.example/return")

    def test_disabled_by_default(self):
        with patch.dict("os.environ", {"PSICOLOGIA_WEBPAY_MODE": "disabled"}, clear=True):
            with self.assertRaises(WebpayNotReady):
                create_test_transaction("empleabilidad", "TEST-100", "session-1", "https://psicologia-carlos-calendar-api.onrender.com/api/webpay/return")

    def test_approved_requires_matching_amount_and_order(self):
        success = {"status": "AUTHORIZED", "response_code": 0, "amount": 45000, "buy_order": "TEST-100"}
        self.assertTrue(is_authorized(success, "empleabilidad", "TEST-100"))
        self.assertFalse(is_authorized(success, "psicoterapia", "TEST-100"))
        self.assertFalse(is_authorized(success, "empleabilidad", "TEST-101"))
        self.assertFalse(is_authorized({**success, "response_code": -1}, "empleabilidad", "TEST-100"))

if __name__ == "__main__":
    unittest.main()
