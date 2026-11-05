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
    if action not in ("register","eligibility"):
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
    if os.environ.get("PSC_FONASA_STATE_SOURCE")!="psc_disk":
        raise StateBridgeUnavailable("SQLite Fonasa cutover not enabled")
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
