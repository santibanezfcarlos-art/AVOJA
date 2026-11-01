"""Offline PSC remote OAuth HMAC tests (fictional ciphertext only)."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from psc_remote_oauth_ciphertext import fetch_encrypted_oauth, RemoteOAuthUnavailable

KEY = "k" * 48
FAKE = "gAAAA" + "X" * 110

class RemoteOAuthTests(unittest.TestCase):
    def test_signed_request_never_contains_secret_in_body(self):
        def transport(req):
            self.assertEqual(req.full_url,
                "https://psicologia-carlos-calendar-api.onrender.com/internal/psc/migration/oauth-ciphertext")
            raw=req.data
            self.assertEqual(json.loads(raw),{"key":"owner"})
            self.assertNotIn(KEY.encode(),raw)
            stamp=req.get_header("X-psc-timestamp")
            signature=req.get_header("X-psc-signature")
            self.assertEqual(signature,
                hmac.new(KEY.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest())
            return json.dumps({"state":"OK","encrypted_refresh_token":FAKE}).encode()
        with patch.dict(os.environ,{"PSC_MIGRATION_BRIDGE_KEY":KEY}):
            self.assertEqual(fetch_encrypted_oauth(transport=transport,now=1234567890),FAKE)

    def test_no_key_fails_closed(self):
        with patch.dict(os.environ,{"PSC_MIGRATION_BRIDGE_KEY":""}):
            with self.assertRaises(RemoteOAuthUnavailable):
                fetch_encrypted_oauth(transport=lambda req:b"{}")

    def test_unavailable_remote_never_returns_any_token(self):
        with patch.dict(os.environ,{"PSC_MIGRATION_BRIDGE_KEY":KEY}):
            for response in (b'{"state":"DISABLED"}',b"not-json",b"{}",
                             b'{"state":"OK","encrypted_refresh_token":"bad"}'):
                with self.assertRaises(RemoteOAuthUnavailable):
                    fetch_encrypted_oauth(transport=lambda req:response)
            def outage(req): raise OSError("simulated offline")
            with self.assertRaises(RemoteOAuthUnavailable):
                fetch_encrypted_oauth(transport=outage)

if __name__=="__main__":
    unittest.main()
