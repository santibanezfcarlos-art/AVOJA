"""Pruebas puramente locales: nunca contactan a Transbank."""
import os
import unittest
from unittest.mock import patch

from webpay_gateway import (
    AMOUNTS, CHECKOUT_PATH, Gateway, GatewayError, GatewaySettings,
    INTEGRATION_HOST, PRODUCTION_HOST, is_authorized, validate_checkout_url,
)


class WebpayGatewayTests(unittest.TestCase):
    def test_default_is_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            g = Gateway()
            self.assertEqual(g.config.environment, "disabled")
            with self.assertRaises(GatewayError):
                g.status("a" * 64)

    def test_production_requires_explicit_approval(self):
        settings = {
            "PSICOLOGIA_PRODUCTION_COMMERCE_CODE": "597012345678",
            "PSICOLOGIA_PRODUCTION_API_KEY": "example-protected-secret-not-real",
        }
        with patch.dict(os.environ, settings, clear=True):
            with self.assertRaisesRegex(GatewayError, "no está autorizado"):
                GatewaySettings.from_environment(mode="production")

    def test_production_does_not_use_sandbox_credentials(self):
        with patch.dict(os.environ, {
            "PSICOLOGIA_PRODUCTION_APPROVED": "true",
            "PSICOLOGIA_PRODUCTION_COMMERCE_CODE": "597012345678",
            "PSICOLOGIA_PRODUCTION_API_KEY": "example-protected-secret-not-real",
        }, clear=True):
            settings = GatewaySettings.from_environment(mode="production")
            self.assertEqual(settings.host, PRODUCTION_HOST)
            self.assertEqual(settings.commerce_code, "597012345678")
            self.assertNotEqual(settings.host, INTEGRATION_HOST)

    def test_validation_does_not_accept_other_websites(self):
        valid = f"https://{PRODUCTION_HOST}{CHECKOUT_PATH}"
        self.assertTrue(validate_checkout_url(valid, PRODUCTION_HOST))
        self.assertFalse(validate_checkout_url(valid, INTEGRATION_HOST))
        self.assertFalse(validate_checkout_url("http://" + PRODUCTION_HOST + CHECKOUT_PATH, PRODUCTION_HOST))
        self.assertFalse(validate_checkout_url(valid + "?redirect=http://bad.test", PRODUCTION_HOST))
        self.assertFalse(validate_checkout_url(f"https://{PRODUCTION_HOST}.attacker.invalid{CHECKOUT_PATH}", PRODUCTION_HOST))

    def test_approved_checks_order_session_amount_and_status(self):
        result = {"status": "AUTHORIZED", "response_code": 0,
                  "buy_order": "PSI12345678", "session_id": "PSIS12345678", "amount": 30000}
        kwargs = {"expected_order": "PSI12345678", "expected_session": "PSIS12345678", "expected_amount": 30000}
        self.assertTrue(is_authorized(result, **kwargs))
        self.assertFalse(is_authorized({**result, "response_code": 1}, **kwargs))
        self.assertFalse(is_authorized({**result, "response_code": True}, **kwargs))
        self.assertFalse(is_authorized({**result, "amount": 45000}, **kwargs))
        self.assertFalse(is_authorized({**result, "status": "INITIALIZED"}, **kwargs))
        self.assertFalse(is_authorized({**result, "buy_order": "PSIWRONGORDER"}, **kwargs))
        self.assertFalse(is_authorized({**result, "session_id": "PSISOTHER"}, **kwargs))

    def test_server_controls_amount_for_each_service(self):
        self.assertEqual(AMOUNTS, {"psicoterapia": 30000, "empleabilidad": 45000, "competencias": 30000})


if __name__ == "__main__":
    unittest.main()
