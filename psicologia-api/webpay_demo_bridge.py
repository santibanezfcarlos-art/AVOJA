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
