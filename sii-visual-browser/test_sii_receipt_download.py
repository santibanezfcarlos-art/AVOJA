"""Offline regression tests; no SII requests, tax issuance or patient email."""
import base64
import sys
import types
import unittest
from unittest.mock import patch

from sii_receipt_download import _pdf_bytes, download_official_pdf

PDF = b"%PDF-1.4\n" + b"verified SII receipt fixture " * 8 + b"\n%%EOF\n"
SNAP = {"folio": "123", "dte": 41, "amount": 150}


class FakePage:
    def __init__(self):
        self.download_calls = 0

    def get_by_role(self, *args, **kwargs):
        self.download_calls += 1
        raise AssertionError("Inline official SII PDF must not click Descargar")

    def wait_for_timeout(self, ms):
        raise AssertionError("Inline official SII PDF must not wait")


class OfflineReceiptTests(unittest.TestCase):
    def test_valid_original_pdf_bytes(self):
        self.assertEqual(_pdf_bytes(base64.b64encode(PDF).decode()), PDF)
        self.assertEqual(_pdf_bytes("data:application/pdf;base64," +
                                    base64.b64encode(PDF).decode()), PDF)

    def test_rejects_invalid_base64_and_non_pdf(self):
        self.assertIsNone(_pdf_bytes("not base64!!"))
        self.assertIsNone(_pdf_bytes(base64.b64encode(b"<html>fake</html>").decode()))

    def test_inline_pdf_bound_to_verified_folio(self):
        page = FakePage()
        current = {**SNAP, "pdf": base64.b64encode(PDF).decode(), "pdf_url": None}
        stub = types.SimpleNamespace(official_snapshot=lambda _, amount: current)
        with patch.dict(sys.modules, {"issue_canary": stub}):
            actual = download_official_pdf(page, SNAP, wait_seconds=0)
        self.assertEqual(base64.b64decode(actual), PDF)
        self.assertEqual(page.download_calls, 0)

    def test_never_accepts_pdf_for_different_folio(self):
        page = FakePage()
        current = {**SNAP, "folio": "999",
                   "pdf": base64.b64encode(PDF).decode()}
        stub = types.SimpleNamespace(official_snapshot=lambda _, amount: current)
        with patch.dict(sys.modules, {"issue_canary": stub}):
            with self.assertRaisesRegex(ValueError, "download_document_changed"):
                download_official_pdf(page, SNAP, wait_seconds=0)
        self.assertEqual(page.download_calls, 0)


if __name__ == "__main__":
    unittest.main()
