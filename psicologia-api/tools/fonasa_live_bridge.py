"""Private asynchronous BonoWeb eligibility lookup. Never clicks Activar."""
import hmac
import os
import re
import secrets
import threading
import time
from urllib.parse import urlparse
from flask import Blueprint, jsonify, request
from playwright.sync_api import sync_playwright

bp = Blueprint("fonasa_bridge", __name__)
_lock=threading.Lock()
_jobs={}
_last_start=0.0
_HOST="bonowebptd.fonasa.cl"

def _auth():
    expected=os.environ.get("PSC_FONASA_BRIDGE_KEY","")
    supplied=request.headers.get("Authorization","")
    return len(expected)>=40 and hmac.compare_digest(supplied,"Bearer "+expected)

def _rut(value):
    if not isinstance(value,str):
        return None
    value=value.replace(".","").replace(" ","").upper()
    match=re.fullmatch(r"(\d{7,8})-([0-9K])",value)
    if not match:return None
    n,dv=match.groups()
    total=sum(int(v)*(2+i%6) for i,v in enumerate(reversed(n)))
    r=11-total%11
    return value if dv==("0" if r==11 else "K" if r==10 else str(r)) else None

def _work(job_id,rut,folio):
    result={"state":"error","activate_available":False,"activated":False}
    try:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
        required=("DECODO_PROXY_HOST","DECODO_PROXY_PORT","DECODO_PROXY_USER",
                  "DECODO_PROXY_PASSWORD","FONASA_MLE_USER","FONASA_MLE_PASSWORD")
        if any(not os.environ.get(k) for k in required):
            result["state"]="configuration_missing"
            return
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"],timeout=11000,
                proxy={"server":"http://"+os.environ["DECODO_PROXY_HOST"]+":"+
                       os.environ["DECODO_PROXY_PORT"],
                       "username":os.environ["DECODO_PROXY_USER"],
                       "password":os.environ["DECODO_PROXY_PASSWORD"]})
            try:
                context=browser.new_context(ignore_https_errors=False)
                context.set_default_timeout(6500)
                context.route("**/*",lambda route:route.abort() if
                    route.request.resource_type in ("image","media","font") else route.continue_())
                page=context.new_page()
                try:
                    page.goto("https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80",
                              wait_until="domcontentloaded",timeout=18000)
                    if urlparse(page.url).hostname!=_HOST:
                        result["state"]="origin_error";return
                    page.locator("#CodUsuario").fill(os.environ["FONASA_MLE_USER"])
                    page.locator("#Password").fill(os.environ["FONASA_MLE_PASSWORD"])
                    page.locator('button[onclick="validafrmLogin()"]').click(timeout=6000)
                    menu=page.get_by_text(re.compile("Activaci[oó]n y desactivar Cuenta M[eé]dica",re.I))
                    menu.first.wait_for(state="visible",timeout=20000)
                    if urlparse(page.url).hostname!=_HOST:
                        result["state"]="origin_error";return
                    menu.first.click(timeout=6000)
                    submenu=page.get_by_text(re.compile("Activaci[oó]n Cuenta M[eé]dica",re.I))
                    submenu.first.click(timeout=6000)
                    for name in ("#RutBeneficiario","#FolioBono","#btnBuscar"):
                        page.locator(name).wait_for(state="visible",timeout=10000)
                    page.locator("#RutBeneficiario").fill(rut)
                    page.locator("#FolioBono").fill(folio)
                    page.locator("#btnBuscar").click(timeout=8000)
                    result["state"]="not_found"
                    # A table row is necessary; no result data is returned.
                    limit=time.monotonic()+13
                    while time.monotonic()<limit:
                        for row in page.locator("table:visible tr").all():
                            cells=row.locator("td").all_inner_texts()
                            if len(cells)<2 or cells[0].strip()!=folio:
                                continue
                            if _rut(cells[1].strip().lstrip("0"))!=rut:
                                continue
                            button=row.get_by_role("button",name=re.compile(r"^\s*Activar\s*$",re.I))
                            if button.count()==1 and button.is_visible() and button.is_enabled():
                                result={"state":"ready_to_activate","activate_available":True,
                                        "activated":False}
                            else:
                                result={"state":"found_without_activation_action",
                                        "activate_available":False,"activated":False}
                            return
                        page.wait_for_timeout(500)
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["state"]="lookup_error"
        result["error_class"]=type(exc).__name__
    finally:
        with _lock:
            _jobs[job_id]={"created":time.monotonic(),"data":result}

@bp.post("/bridge/fonasa/start")
def start():
    global _last_start
    if not _auth(): return jsonify(error="unauthorized"),403
    body=request.get_json(silent=True) or {}
    rut=_rut(body.get("rut"))
    folio=body.get("folio")
    if not rut or not isinstance(folio,str) or not re.fullmatch(r"\d{5,30}",folio):
        return jsonify(error="invalid_bono"),400
    with _lock:
        now=time.monotonic()
        for key in list(_jobs):
            if now-_jobs[key]["created"]>1800:del _jobs[key]
        if any(v["data"].get("state")=="running" for v in _jobs.values()):
            return jsonify(error="another_lookup_running"),429
        if now-_last_start<10:return jsonify(error="retry_later"),429
        _last_start=now
        job=secrets.token_urlsafe(22)
        _jobs[job]={"created":now,"data":{"state":"running","activate_available":False}}
    threading.Thread(target=_work,args=(job,rut,folio),daemon=True).start()
    return jsonify(job_id=job,state="running"),202

@bp.get("/bridge/fonasa/status/<job>")
def status(job):
    if not _auth():return jsonify(error="unauthorized"),403
    with _lock:
        item=_jobs.get(job)
        if not item:return jsonify(error="job_unknown_or_expired"),404
        return jsonify(item["data"])
