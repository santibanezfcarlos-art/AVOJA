"""Canal privado firmado para completar ensayos Webpay en el backend de agenda.

Solo comunicación servidor-servidor, con claves almacenadas en Render.
Ante errores de Calendar o correo, la compra NO se presenta como cita real.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

URL="https://psicologia-retenciones-piloto.onrender.com"
KEY_NAME="PSICOLOGIA_DEMO_BRIDGE_KEY"

class BridgeError(Exception):
    def __init__(self, state="UNAVAILABLE"):
        self.state=state
        super().__init__(state)

def enabled():
    return (os.environ.get("PSICOLOGIA_DEMO_BRIDGE_ENABLED")=="true"
            and len(os.environ.get(KEY_NAME,""))>=40
            and os.environ.get("PSICOLOGIA_WEBPAY_MODE")=="integration")

def _request(endpoint,data):
    if not enabled():
        raise BridgeError("BRIDGE_DISABLED")
    raw=json.dumps(data,separators=(",",":"),ensure_ascii=True,sort_keys=True).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(os.environ[KEY_NAME].encode(),
                       stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    req=Request(URL+endpoint,data=raw,method="POST",headers={
        "Content-Type":"application/json",
        "X-Psc-Timestamp":stamp,"X-Psc-Signature":signature,
        "User-Agent":"pscsantibanez-integration-server"})
    try:
        with urlopen(req,timeout=18) as result:
            response=json.loads(result.read(2500).decode())
    except HTTPError as exc:
        try:
            response=json.loads(exc.read(2500).decode())
        except Exception:
            raise BridgeError("UNAVAILABLE") from None
        raise BridgeError(response.get("state","UNAVAILABLE")) from None
    except (URLError,TimeoutError,OSError,ValueError):
        raise BridgeError("UNAVAILABLE") from None
    if not isinstance(response,dict) or not isinstance(response.get("state"),str):
        raise BridgeError("INVALID_REPLY")
    return response["state"]

def preflight(*,order,session,service,day,hour,amount,email,email_consent=False,minutes=None,patient_name=None):
    return _request("/internal/psicologia/demo/preflight",{
        "order":order,"session":session,"service":service,"date":day,
        "time":hour,"amount":amount,"email":email,"email_consent":email_consent,"minutes":minutes,"patient_name":patient_name})

def complete(*,order,session,amount,outcome):
    return _request("/internal/psicologia/demo/complete",{
        "order":order,"session":session,"amount":amount,"outcome":outcome})

def busy_intervals(first,last,include_calendar=False):
    """Private authenticated read of held slots and optional live Google occupied times."""
    if not enabled():
        raise BridgeError("BRIDGE_DISABLED")
    data={"from":first.isoformat(),"to":last.isoformat()}
    if include_calendar:
        data["include_calendar"]=True
    raw=json.dumps(data,separators=(",",":"),ensure_ascii=True,sort_keys=True).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(os.environ[KEY_NAME].encode(),
                       stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    req=Request(URL+"/internal/psicologia/demo/busy",data=raw,method="POST",
       headers={"Content-Type":"application/json","X-Psc-Timestamp":stamp,
                "X-Psc-Signature":signature})
    try:
        with urlopen(req,timeout=35) as response:
            payload=json.loads(response.read(30000).decode())
    except (HTTPError,URLError,TimeoutError,OSError,ValueError):
        raise BridgeError("BUSY_UNAVAILABLE") from None
    if payload.get("state")!="OK" or not isinstance(payload.get("intervals"),list):
        raise BridgeError("BUSY_UNAVAILABLE")
    from datetime import datetime
    intervals=[]
    for pair in payload["intervals"]:
        if not isinstance(pair,list) or len(pair)!=2:
            raise BridgeError("BUSY_UNAVAILABLE")
        start,end=(datetime.fromisoformat(x) for x in pair)
        if start.tzinfo is None or end.tzinfo is None or end<=start:
            raise BridgeError("BUSY_UNAVAILABLE")
        intervals.append((start,end))
    return intervals

def confirm_fonasa_booking(*,intake_id,day,hour,patient_name,minutes):
    """Signed Google confirmation; no Transbank payment is fabricated."""
    if not enabled():
        raise BridgeError("BRIDGE_DISABLED")
    data={"intake_id":intake_id,"date":day,"time":hour,
          "patient_name":patient_name,"minutes":minutes}
    raw=json.dumps(data,separators=(",",":"),ensure_ascii=True,sort_keys=True).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(os.environ[KEY_NAME].encode(),
                       stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    req=Request(URL+"/internal/psicologia/fonasa/confirm",data=raw,method="POST",
        headers={"Content-Type":"application/json","X-Psc-Timestamp":stamp,
                 "X-Psc-Signature":signature})
    try:
        with urlopen(req,timeout=50) as response:
            body=json.loads(response.read(4000).decode())
    except HTTPError as exc:
        try:
            body=json.loads(exc.read(2000).decode())
        except (ValueError,OSError):
            raise BridgeError("CALENDAR_UNAVAILABLE") from None
    except (URLError,TimeoutError,OSError,ValueError):
        raise BridgeError("CALENDAR_UNAVAILABLE") from None
    state=body.get("state")
    if state!="CONFIRMED":
        raise BridgeError(state if isinstance(state,str) else "CALENDAR_UNAVAILABLE")
    event=body.get("event_id")
    meet=body.get("meet_url")
    if not isinstance(event,str) or len(event)>110:
        raise BridgeError("UNVERIFIED_EVENT")
    if meet is not None and (not isinstance(meet,str) or not meet.startswith("https://meet.google.com/")):
        meet=None
    return {"event_id":event,"meet_url":meet}
