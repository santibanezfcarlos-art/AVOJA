"""Short-lived, operator-only read-only SII diagnosis. No issuance route."""
import os,time,hmac,json,secrets,hashlib
from flask import Blueprint,jsonify,request
import sii_canary
from sii_one_time_recovery import TARGET_ORDER
from sii_visual_admin import _b64
bp=Blueprint("sii_diagnostic_maintenance",__name__)
def allowed():
    token=os.environ.get("PSC_SII_DIAGNOSTIC_TOKEN","")
    try:valid=time.time()<int(os.environ.get("PSC_SII_DIAGNOSTIC_EXPIRES","0"))
    except ValueError:valid=False
    return valid and len(token)>=32 and hmac.compare_digest(token,request.headers.get("X-PSC-Diagnostic",""))
@bp.post("/admin/sii-diagnostic/start")
def start():
    if not allowed():return jsonify(error="no_autorizado"),403
    base,secret=sii_canary._visual_service()
    payload=_b64(json.dumps({"exp":int(time.time())+600,"nonce":secrets.token_urlsafe(24),"preview_order":TARGET_ORDER,"mode":"diagnostic"},separators=(",",":")).encode())
    signature=_b64(hmac.new(secret.encode(),payload.encode("ascii"),hashlib.sha256).digest())
    try:
        status,result=sii_canary._server_request(base+"/api/internal/start",secret,{"ticket":payload+"."+signature})
        return jsonify(started=status==202),status
    except Exception:return jsonify(error="diagnostic_not_started"),503
@bp.get("/admin/sii-diagnostic/status")
def status():
    if not allowed():return jsonify(error="no_autorizado"),403
    from urllib.parse import urlencode
    base,secret=sii_canary._visual_service()
    try:
        _,result=sii_canary._server_request(base+"/api/internal/status?"+urlencode({"order_id":TARGET_ORDER}),secret)
        return jsonify(result)
    except Exception:return jsonify(error="diagnostic_unavailable"),503
