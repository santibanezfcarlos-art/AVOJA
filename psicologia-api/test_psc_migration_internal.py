"""Offline integration tests for disabled internal PSC migration endpoint.

Invented appointment times only. Does not import production main application,
send network requests, access Google, contact Webpay, or write to real storage.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from flask import Flask

from psc_migration_internal import bp

KEY = "test-migration-hmac-secret-" + "k" * 48


def make_db(directory):
    path = Path(directory) / "fake-holds.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE psychology_booking_holds (
            slot_date TEXT, starts_at TEXT, ends_at TEXT,
            status TEXT, expires_at TEXT)""")
        values = [
            ("2026-11-12", "2026-11-12T15:00:00+00:00",
             "2026-11-12T15:45:00+00:00", "CONFIRMED", "2026-11-12T16:00:00+00:00"),
            ("2026-11-12", "2026-11-12T16:00:00+00:00",
             "2026-11-12T16:45:00+00:00", "PAYMENT_PENDING", "2099-01-01T00:00:00+00:00"),
            ("2026-11-12", "2026-11-12T17:00:00+00:00",
             "2026-11-12T17:45:00+00:00", "RECONCILIATION_REQUIRED", "2099-01-01T00:00:00+00:00"),
            ("2026-11-12", "2026-11-12T18:00:00+00:00",
             "2026-11-12T18:45:00+00:00", "HELD", "2020-01-01T00:00:00+00:00"),
        ]
        db.executemany("INSERT INTO psychology_booking_holds VALUES(?,?,?,?,?)", values)
    return path


class ShadowEndpointTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PSC_MIGRATION_SHADOW_READ_ENABLED":"true",
            "PSC_MIGRATION_BRIDGE_KEY": KEY,
        }, clear=False)
        self.env.start()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.env.stop()

    def signed_post(self, data, secret=KEY):
        raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), stamp.encode()+b"."+raw,
                             hashlib.sha256).hexdigest()
        return self.client.post(
            "/internal/psc/migration/holds-only", data=raw,
            headers={"Content-Type":"application/json","X-Psc-Timestamp":stamp,
                     "X-Psc-Signature":signature}
        )

    def test_dormant_unless_explicitly_enabled(self):
        with patch.dict(os.environ, {"PSC_MIGRATION_SHADOW_READ_ENABLED":"false"}):
            response = self.signed_post({"from":"2026-11-12","to":"2026-11-12"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json["state"], "DISABLED")

    def test_rejects_invalid_signature(self):
        response = self.signed_post({"from":"2026-11-12","to":"2026-11-12"},
                                    secret="wrong-secret")
        self.assertEqual(response.status_code,403)

    def test_ignores_expired_and_adds_full_day_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_db(tmp)
            with patch("psc_holds_sqlite.configured_file",return_value=path):
                response = self.signed_post({"from":"2026-11-12","to":"2026-11-12"})
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json["state"],"OK")
            self.assertEqual(len(response.json["intervals"]),4)
            self.assertFalse(response.json["includes_google"])
            self.assertIn("no-store",response.headers.get("Cache-Control",""))
            self.assertNotIn("patient",json.dumps(response.json).lower())

    def test_rejects_calendar_claim_and_extra_input(self):
        response = self.signed_post({"from":"2026-11-12","to":"2026-11-12",
                                     "include_calendar":True})
        self.assertEqual(response.status_code,400)

    def test_oauth_ciphertext_is_disabled_by_default(self):
        with patch.dict(os.environ, {"PSC_MIGRATION_OAUTH_READ_ENABLED":"false"}):
            response=self.client.post("/internal/psc/migration/oauth-ciphertext",
                                      json={"key":"owner"})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json["state"],"DISABLED")

    def test_oauth_requires_hmac_and_never_returns_plaintext(self):
        with patch.dict(os.environ, {"PSC_MIGRATION_OAUTH_READ_ENABLED":"true"}):
            unsigned=self.client.post("/internal/psc/migration/oauth-ciphertext",
                                      json={"key":"owner"})
            self.assertEqual(unsigned.status_code,403)
            with patch("psc_migration_internal.PSCStore") as fake:
                fake.return_value.encrypted_google_oauth.return_value="gAAAA-CIPHERTEXT"
                valid=self.signed_post_to(
                    "/internal/psc/migration/oauth-ciphertext",{"key":"owner"})
            self.assertEqual(valid.status_code,200)
            self.assertEqual(valid.json["encrypted_refresh_token"],"gAAAA-CIPHERTEXT")
            self.assertNotIn("plaintext",json.dumps(valid.json).lower())
            self.assertIn("no-store",valid.headers.get("Cache-Control",""))

    def signed_post_to(self, path, data, secret=KEY):
        raw=json.dumps(data, separators=(",",":"), sort_keys=True).encode()
        stamp=str(int(time.time()))
        signature=hmac.new(secret.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
        return self.client.post(path,data=raw,
                headers={"Content-Type":"application/json","X-Psc-Timestamp":stamp,
                         "X-Psc-Signature":signature})

    def test_fails_closed_if_store_unavailable(self):
        response = self.signed_post({"from":"2026-11-12","to":"2026-11-12"})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json["state"],"UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
