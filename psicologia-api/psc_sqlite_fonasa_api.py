"""Signed opt-in Fonasa state adapter, only main PSC API owns /var/data.

Not registered until the full migration cutover is approved and verified.
No unencrypted rebooking tokens are stored or returned by this endpoint.
"""
import hashlib,hmac,json,os,re,time
from datetime import datetime,timezone
from flask import Blueprint,jsonify,request
from psc_sqlite_state import StateUnavailable
from psc_sqlite_fonasa import register,eligibility,prepare_change,commit_change,lookup,busy_intervals,cancel_deleted,FonasaStateConflict

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

@bp.post(PATH+"/prepare")
def prepare():
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("prepare"):return jsonify(state="NOT_AUTHORIZED"),403
    doc=request.get_json(silent=True)
    if not isinstance(doc,dict) or set(doc)!={"source_id","token_sha256","start","end"}:
        return jsonify(state="INVALID"),400
    try:
        state=prepare_change(source_id=doc["source_id"],
            token_sha256=doc["token_sha256"],
            new_start=datetime.fromisoformat(doc["start"]),
            new_end=datetime.fromisoformat(doc["end"]))
    except (ValueError,TypeError):return jsonify(state="INVALID"),400
    except FonasaStateConflict:return jsonify(state="CONFLICT"),409
    except Exception:return jsonify(state="UNAVAILABLE"),503
    return jsonify(state=state)

@bp.post(PATH+"/commit")
def commit():
    # Only call AFTER signed Calendar service has verified its own event.
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("commit"):return jsonify(state="NOT_AUTHORIZED"),403
    doc=request.get_json(silent=True)
    if not isinstance(doc,dict) or set(doc)!={"source_id","token_sha256","start","end"}:
        return jsonify(state="INVALID"),400
    try:
        state=commit_change(source_id=doc["source_id"],
            token_sha256=doc["token_sha256"],
            new_start=datetime.fromisoformat(doc["start"]),
            new_end=datetime.fromisoformat(doc["end"]))
    except (ValueError,TypeError):return jsonify(state="INVALID"),400
    except FonasaStateConflict:return jsonify(state="CONFLICT"),409
    except Exception:return jsonify(state="UNAVAILABLE"),503
    return jsonify(state=state)

@bp.post(PATH+"/lookup")
def lookup_token():
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("lookup"):return jsonify(state="NOT_AUTHORIZED"),403
    doc=request.get_json(silent=True)
    if not isinstance(doc,dict) or set(doc)!={"token_sha256"}:
        return jsonify(state="INVALID"),400
    try:
        row=lookup(token_sha256=doc["token_sha256"])
        if not row:return jsonify(state="NOT_FOUND"),404
        view={"order_id":row["source_id"],"hold_id":row["hold_id"],
              "patient_name":row["patient_name"],
              "test_recipient":row["patient_email"],
              "slot_date":row["slot_date"],"starts_at":row["starts_at"],
              "ends_at":row["ends_at"],"service":row["service"],
              "state":row["status"],"source":"fonasa"}
        return jsonify(state="FOUND",booking=view,
                       eligibility=eligibility(token_sha256=doc["token_sha256"]))
    except Exception:return jsonify(state="UNAVAILABLE"),503

@bp.post(PATH+"/busy")
def busy():
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("busy"):return jsonify(state="NOT_AUTHORIZED"),403
    doc=request.get_json(silent=True)
    if not isinstance(doc,dict) or not set(doc) in (
          {"date"},{"date","exclude_hold_id"}):
        return jsonify(state="INVALID"),400
    try:
        from datetime import date
        day=date.fromisoformat(doc["date"])
        if abs((day-datetime.now(timezone.utc).date()).days)>61:
            return jsonify(state="INVALID"),400
        windows=busy_intervals(day=day,exclude_hold_id=doc.get("exclude_hold_id"))
        return jsonify(state="OK",intervals=[[a.isoformat(),b.isoformat()] for a,b in windows])
    except (ValueError,TypeError):return jsonify(state="INVALID"),400
    except Exception:return jsonify(state="UNAVAILABLE"),503

@bp.post(PATH+"/reset")
def reset():
    # The calling service must verify the event was deleted from Google first.
    if request.content_length is None or not 10<request.content_length<=MAX_SIZE:
        return jsonify(state="INVALID"),413
    if not _signed("reset"):return jsonify(state="NOT_AUTHORIZED"),403
    doc=request.get_json(silent=True)
    if not isinstance(doc,dict) or set(doc)!={"source_id","hold_id"}:
        return jsonify(state="INVALID"),400
    try:
        state=cancel_deleted(source_id=doc["source_id"],hold_id=doc["hold_id"])
    except (ValueError,TypeError):return jsonify(state="INVALID"),400
    except FonasaStateConflict:return jsonify(state="CONFLICT"),409
    except Exception:return jsonify(state="UNAVAILABLE"),503
    return jsonify(state=state)
