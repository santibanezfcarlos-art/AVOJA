"""PSC pilot's optional, disabled-by-default OAuth ciphertext RPC.

Use only after migrated SQLite is validated on the paid API and both
private HMAC credentials are configured intentionally. An unavailable
response FAILS CLOSED; never falls back to unauthenticated OAuth or an
unrelated Google identity. Does NOT decrypt or log the credential.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT=("https://psicologia-carlos-calendar-api.onrender.com"
          "/internal/psc/migration/oauth-ciphertext")


class RemoteOAuthUnavailable(RuntimeError):
    pass


class _BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never forward an authenticated RPC request to a new host.
        return None


def fetch_encrypted_oauth(*, transport=None, now=None):
    key=os.environ.get("PSC_MIGRATION_BRIDGE_KEY","")
    if len(key)<40:
        raise RemoteOAuthUnavailable("Migration RPC authentication not configured")
    body=json.dumps({"key":"owner"},sort_keys=True,separators=(",",":")).encode("utf-8")
    stamp=str(int(now if now is not None else time.time()))
    signature=hmac.new(key.encode(),stamp.encode()+b"."+body,hashlib.sha256).hexdigest()
    req=Request(ENDPOINT,data=body,method="POST",
                headers={"Content-Type":"application/json",
                         "X-Psc-Timestamp":stamp,
                         "X-Psc-Signature":signature})
    try:
        if transport is None:
            def fetch(request):
                with build_opener(_BlockRedirect()).open(request,timeout=8) as response:
                    return response.read(8193)
            transport=fetch
        response=transport(req)
        if not isinstance(response,(bytes,bytearray)) or len(response)>8192:
            raise RemoteOAuthUnavailable("Bad OAuth RPC response")
        data=json.loads(response)
        if not isinstance(data,dict) or data.get("state")!="OK":
            raise RemoteOAuthUnavailable("OAuth ciphertext unavailable")
        ciphertext=data.get("encrypted_refresh_token")
        if not isinstance(ciphertext,str) or not 80<=len(ciphertext)<=8000:
            raise RemoteOAuthUnavailable("Invalid ciphertext")
        return ciphertext
    except (OSError,URLError,ValueError,TypeError,KeyError):
        raise RemoteOAuthUnavailable("OAuth RPC unavailable") from None
