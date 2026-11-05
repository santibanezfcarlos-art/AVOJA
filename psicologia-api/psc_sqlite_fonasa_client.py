"""Opt-in Fonasa SQLite state client; no writes until full cutover.

Only API principal has /var/data. This pilot must use the existing secret to
sign narrow domain operations, never arbitrary SQL. No token plaintext sent.
"""
import hashlib,hmac,json,os,time
from datetime import datetime
from urllib.request import Request,urlopen

BASE="https://psicologia-carlos-calendar-api.onrender.com/internal/psc/sqlite/fonasa"

class StateBridgeUnavailable(RuntimeError):pass

def call(action,doc):
    if action not in ("register","eligibility","prepare","commit","lookup","busy","reset","abort"):
        raise ValueError("Unsupported PSC state operation")
    secret=os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY","")
    if len(secret)<40:
        raise StateBridgeUnavailable("Missing bridge key")
    raw=json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    stamp=str(int(time.time()))
    signed=stamp.encode()+b"."+action.encode()+b"."+raw
    sig=hmac.new(secret.encode(),signed,hashlib.sha256).hexdigest()
    req=Request(BASE+"/"+action,data=raw,method="POST",headers={
        "Content-Type":"application/json",
        "X-PSC-State-Timestamp":stamp,
        "X-PSC-State-Signature":sig})
    try:
        with urlopen(req,timeout=12) as response:
            body=json.loads(response.read(3000))
    except Exception as exc:
        raise StateBridgeUnavailable(type(exc).__name__) from None
    if not isinstance(body,dict) or not isinstance(body.get("state"),str):
        raise StateBridgeUnavailable("Invalid response")
    return body["state"]

def register_fonasa(source,hold_id,name,email,start,end):
    if (os.environ.get("PSC_FONASA_STATE_SOURCE")!="psc_disk"
            or os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true"):
        raise StateBridgeUnavailable("SQLite Fonasa cutover not fully verified")
    key=os.environ.get("PSICOLOGIA_REBOOKING_TOKEN_SECRET","")
    if len(key)<40:
        raise StateBridgeUnavailable("Missing rebooking token key")
    token=hmac.new(key.encode(),("fonasa-rebooking-v1:"+source).encode(),hashlib.sha256).hexdigest()
    document={"source_id":source,"hold_id":str(hold_id),
       "token_sha256":hashlib.sha256(token.encode()).hexdigest(),
       "name":name,"email":email,
       "start":start.isoformat(),"end":end.isoformat()}
    if call("register",document)!="REGISTERED":
        raise StateBridgeUnavailable("PSC state registration not acknowledged")
    return token

def check_token(token):
    return call("eligibility",{"token_sha256":hashlib.sha256(token.encode()).hexdigest()})

def prepare_rebooking(source,token,start,end):
    if os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true":
        raise StateBridgeUnavailable("Fonasa migration not enabled")
    data={"source_id":source,"token_sha256":hashlib.sha256(token.encode()).hexdigest(),
          "start":start.isoformat(),"end":end.isoformat()}
    return call("prepare",data)

def commit_rebooking(source,token,start,end):
    if os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true":
        raise StateBridgeUnavailable("Fonasa migration not enabled")
    data={"source_id":source,"token_sha256":hashlib.sha256(token.encode()).hexdigest(),
          "start":start.isoformat(),"end":end.isoformat()}
    return call("commit",data)

def details(token):
    """Server-side only: booking identifiers and contact; never log response."""
    if os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true":
        raise StateBridgeUnavailable("Migration disabled")
    digest=hashlib.sha256(token.encode()).hexdigest()
    return call_document("lookup",{"token_sha256":digest})

def destination_busy(day,exclude_hold_id=None):
    doc={"date":day.isoformat()}
    if exclude_hold_id:doc["exclude_hold_id"]=str(exclude_hold_id)
    data=call_document("busy",doc)
    if data.get("state")!="OK":raise StateBridgeUnavailable("Busy state unavailable")
    return [(datetime.fromisoformat(a),datetime.fromisoformat(b))
             for a,b in data.get("intervals",[])]

def reset_deleted(source,hold_id):
    return call("reset",{"source_id":source,"hold_id":str(hold_id)})

def call_document(action,doc):
    if action not in ("register","eligibility","prepare","commit","lookup","busy","reset","abort"):
        raise ValueError("Invalid state action")
    secret=os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY","")
    if len(secret)<40:raise StateBridgeUnavailable("Missing bridge key")
    raw=json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(secret.encode(),
        stamp.encode()+b"."+action.encode()+b"."+raw,hashlib.sha256).hexdigest()
    req=Request(BASE+"/"+action,data=raw,method="POST",headers={
        "Content-Type":"application/json",
        "X-PSC-State-Timestamp":stamp,
        "X-PSC-State-Signature":signature})
    try:
        with urlopen(req,timeout=12) as response:
            payload=json.loads(response.read(5000))
    except Exception as exc:
        raise StateBridgeUnavailable(type(exc).__name__) from None
    if not isinstance(payload,dict) or not isinstance(payload.get("state"),str):
        raise StateBridgeUnavailable("Invalid state reply")
    return payload

def abort_rebooking(source,token,start,end):
    if os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true":
        raise StateBridgeUnavailable("Migration disabled")
    return call("abort",{"source_id":source,
        "token_sha256":hashlib.sha256(token.encode()).hexdigest(),
        "start":start.isoformat(),"end":end.isoformat()})
