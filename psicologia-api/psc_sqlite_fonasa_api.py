"""Signed opt-in Fonasa state adapter, only main PSC API owns /var/data.

Not registered until the full migration cutover is approved and verified.
No unencrypted rebooking tokens are stored or returned by this endpoint.
"""
import hashlib,hmac,json,os,re,time
from datetime import datetime,timezone
from flask import Blueprint,jsonify,request
from psc_sqlite_state import StateUnavailable
from psc_sqlite_fonasa import register,eligibility

bp=Blueprint("psc_sqlite_state_rpc",__name__)
PATH="/internal/psc/sqlite/fonasa"
MAX_SIZE=2200

def _signed(endpoint):
    if os.environ.get("PSC_SQLITE_STATE_ENABLED")!="true" or os.environ.get("PSC_SQLITE_BACKEND_RPC_ENABLED")!="true":
        return False
    secret=os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY","")
    timestamp=request.headers.get("X-PSC-State-Timestamp","")
    signature=request.headers.get("X-PSC-State-Signature","")
    if (len(secret)<40 or not re.fullmatch(r"[0-9]{10}",timestamp)
            or not re.fullmatch(r"[0-9a-f]{64}",signature)
            or abs(int(timestamp)-int(time.time()))>120):
        return False
    body=request.get_data(cache=True)
    payload=timestamp.encode()+b"."+endpoint.encode()+b"."+body
    expected=hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature,expected)

@bp.post(PATH+"/register")
def add():
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("register"):return jsonify(state="NOT_AUTHORIZED"),403
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or set(data)!={"source_id","hold_id","token_sha256","name","email","start","end"}:
        return jsonify(state="INVALID"),400
    try:
        state=register(source_id=data["source_id"],hold_id=data["hold_id"],
            token_sha256=data["token_sha256"],name=data["name"],email=data["email"],
            start=datetime.fromisoformat(data["start"]),end=datetime.fromisoformat(data["end"]))
    except ValueError:return jsonify(state="INVALID"),400
    except StateUnavailable:return jsonify(state="UNAVAILABLE"),503
    except Exception:return jsonify(state="CONFLICT_OR_UNAVAILABLE"),409
    return jsonify(state=state)

@bp.post(PATH+"/eligibility")
def check():
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("eligibility"):return jsonify(state="NOT_AUTHORIZED"),403
    data=request.get_json(silent=True)
    if not isinstance(data,dict) or set(data)!={"token_sha256"}:
        return jsonify(state="INVALID"),400
    try:return jsonify(state=eligibility(token_sha256=data["token_sha256"]))
    except StateUnavailable:return jsonify(state="UNAVAILABLE"),503
    except Exception:return jsonify(state="UNAVAILABLE"),503
