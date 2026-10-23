"""Pruebas unitarias SIN llamadas a Transbank y SIN cargos reales."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webpay_gateway import WebpayGateway, WebpayError, approved_payment, SESSION_PRICE_CLP


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.order = "PSIABCD12345678"
        self.session = "PSISABCD12345678"
        self.token = "A" * 60
        self.checkout = "https://webpay3gint.transbank.cl/webpayserver/initTransaction"

    def result(self, **changes):
        item = {
            "status": "AUTHORIZED", "response_code": 0,
            "amount": 30_000, "buy_order": self.order,
            "session_id": self.session
        }
        item.update(changes)
        return item

    def test_approved_response_must_match_all_server_side_values(self):
        self.assertTrue(approved_payment(self.result(), buy_order=self.order, session_id=self.session))
        for mutated in [
            self.result(status="FAILED"), self.result(response_code=1),
            self.result(response_code=True), self.result(amount=100),
            self.result(amount=30001), self.result(buy_order="PSIOTHER12345"),
            self.result(session_id="PSISOTHER12345")
        ]:
            self.assertFalse(approved_payment(mutated, buy_order=self.order, session_id=self.session))
        self.assertFalse(approved_payment(self.result(), buy_order=self.order, session_id=self.session, amount=100))

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"PSICOLOGIA_WEBPAY_MODE": "disabled"}):
            self.assertEqual(WebpayGateway().mode, "disabled")
        with self.assertRaises(WebpayError):
            WebpayGateway(mode="disabled").create_for_existing_hold(
                buy_order=self.order, session_id=self.session)

    def test_only_official_checkout_origin(self):
        gateway = WebpayGateway(mode="integration")
        self.assertTrue(gateway.validate_checkout_url(self.checkout))
        for url in [
            "http://webpay3gint.transbank.cl/webpayserver/initTransaction",
            "https://evil.example/webpayserver/initTransaction",
            "https://webpay3gint.transbank.cl.evil.com/webpayserver/initTransaction",
            "https://webpay3gint.transbank.cl/webpayserver/initTransaction?redirect=evil",
            "https://webpay3gint.transbank.cl/other"
        ]:
            self.assertFalse(gateway.validate_checkout_url(url), url)

    def test_checkout_forces_30000_even_when_client_would_supply_other_amount(self):
        gateway = WebpayGateway(mode="integration")
        captured = {}
        def fake_request(method, **kwargs):
            captured.update(kwargs)
            self.assertEqual(method, "POST")
            return {"token": self.token, "url": self.checkout}
        gateway._request = fake_request
        resp = gateway.create_for_existing_hold(
            buy_order=self.order, session_id=self.session)
        self.assertEqual(resp["url"], self.checkout)
        self.assertEqual(captured["data"]["amount"], SESSION_PRICE_CLP)
        self.assertEqual(captured["data"]["return_url"],
                         "https://psicologia-carlos-calendar-api.onrender.com/api/webpay/return")
        self.assertEqual(gateway.commerce_code, WebpayGateway.TEST_CODE)

    def test_production_requires_separate_opt_in_and_own_credentials(self):
        with patch.dict(os.environ, {"PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED": "false"}):
            with self.assertRaises(ValueError):
                WebpayGateway(mode="production")
        with patch.dict(os.environ, {
            "PSICOLOGIA_WEBPAY_PRODUCTION_APPROVED": "true",
            "PSICOLOGIA_WEBPAY_COMMERCE_CODE": "",
            "PSICOLOGIA_WEBPAY_API_KEY": "",
        }):
            with self.assertRaises(ValueError):
                WebpayGateway(mode="production")


if __name__ == "__main__":
    unittest.main()
