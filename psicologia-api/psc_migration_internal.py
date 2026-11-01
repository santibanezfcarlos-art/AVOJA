"""PSC internal migration API, not connected to current public availability.

HMAC-protected and disabled by default. Returns ONLY held/confirmed times,
not Google Calendar occupancy. It is never a replacement for include_calendar
until the independent Google dependency is migrated and validated.
"""
from datetime import date
import hashlib
import hmac
import os
import re
import time

from flask import Blueprint, jsonify, request

from psc_holds_sqlite import read_intervals
from psc_sqlite_txn_store import PSCStore

bp = Blueprint("psc_migration_holds_only", __name__)


def _enabled():
    return (
        os.environ.get("PSC_MIGRATION_SHADOW_READ_ENABLED") == "true"
        and len(os.environ.get("PSC_MIGRATION_BRIDGE_KEY", "")) >= 40
    )


def _authorized():
    # Authorization is independent of each endpoint's feature flag; the flag
    # is checked by the route BEFORE this helper is called.
    if len(os.environ.get("PSC_MIGRATION_BRIDGE_KEY", "")) < 40:
        return False
    if not request.is_json or (request.content_length or 0) > 1024:
        return False
    stamp = request.headers.get("X-Psc-Timestamp", "")
    signature = request.headers.get("X-Psc-Signature", "")
    if not re.fullmatch(r"[0-9]{10,12}", stamp):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    if abs(int(time.time()) - int(stamp)) > 120:
        return False
    expected = hmac.new(
        os.environ["PSC_MIGRATION_BRIDGE_KEY"].encode("utf-8"),
        stamp.encode() + b"." + request.get_data(cache=True),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@bp.post("/internal/psc/migration/holds-only")
def holds_only():
    if not _enabled():
        return jsonify(state="DISABLED"), 503
    if not _authorized():
        return jsonify(state="NOT_AUTHORIZED"), 403
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"from", "to"}:
        return jsonify(state="INVALID"), 400
    try:
        first = date.fromisoformat(payload["from"])
        last = date.fromisoformat(payload["to"])
        windows = read_intervals(first, last)
        reply = jsonify(state="OK", intervals=windows, includes_google=False)
        reply.headers["Cache-Control"] = "no-store"
        return reply
    except (TypeError, ValueError):
        return jsonify(state="INVALID"), 400
    except Exception:
        # Never return paths, calendar details, patients, tokens, errors or SQL.
        return jsonify(state="UNAVAILABLE"), 503

@bp.post("/internal/psc/migration/oauth-ciphertext")
def oauth_ciphertext():
    """Opt-in private migration RPC: Fernet ciphertext only, never plaintext.

    The recipient still needs the existing Fernet key to decrypt locally. The
    feature is OFF unless intentionally activated on the paid service.
    """
    if os.environ.get("PSC_MIGRATION_OAUTH_READ_ENABLED") != "true":
        return jsonify(state="DISABLED"), 503
    if not _authorized():
        return jsonify(state="NOT_AUTHORIZED"), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data != {"key":"owner"}:
        return jsonify(state="INVALID"), 400
    try:
        secret = PSCStore().encrypted_google_oauth("owner")
        if not secret:
            return jsonify(state="UNAVAILABLE"), 503
        reply=jsonify(state="OK", encrypted_refresh_token=secret)
        reply.headers["Cache-Control"]="no-store"
        return reply
    except Exception:
        return jsonify(state="UNAVAILABLE"), 503
