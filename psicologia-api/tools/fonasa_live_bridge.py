"""Private BonoWeb lookup and operator-requested activation of a matched voucher."""
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

def _row_status(row):
    """Only explicit state on the matched voucher row confirms activation."""
    reverse=row.get_by_role("button",name=re.compile(r"^\s*Desactivar\s*$",re.I))
    if reverse.count()==1 and reverse.is_visible() and reverse.is_enabled():return True
    cells=[re.sub(r"\s+"," ",x).strip().casefold() for x in row.locator("td").all_inner_texts()]
    return any(x in {"activado","activada","cuenta médica activada","bono activado"} for x in cells)


def _activate_matched(page,row,rut,folio):
    """A click is a request, never proof of completion."""
    control=row.get_by_role("button",name=re.compile(r"^\s*Activar\s*$",re.I))
    dialog_unknown=[]
    def dialog_handler(dialog):
        if dialog.type=="confirm" and re.search(r"\bactivar\b|\bactivaci[oó]n\b",dialog.message,re.I):
            dialog.accept()
        elif dialog.type=="alert" and re.search(r"activad[oa]|activaci[oó]n",dialog.message,re.I):
            dialog.dismiss()
        else:
            dialog_unknown.append(True)
            dialog.dismiss()
    page.on("dialog",dialog_handler)
    # Mark as dispatched before clicking: a timeout can occur after server receipt.
    result={"state":"activation_unconfirmed","activated":False,"activation_requested":True,
            "activate_available":False,"stage":"activation_submitted"}
    try:
        control.click(timeout=8000)
        limit=time.monotonic()+12
        while time.monotonic()<limit:
            if dialog_unknown:return result
            for candidate in page.locator("table:visible tr").all():
                cells=candidate.locator("td").all_inner_texts()
                if len(cells)<2 or cells[0].strip()!=folio or _rut(cells[1].strip().lstrip("0"))!=rut:continue
                if _row_status(candidate):
                    return {**result,"state":"activated","activated":True,"stage":"activation_verified"}
            page.wait_for_timeout(500)
        # Re-query the same voucher once; absence of Activar alone is never proof.
        search=page.locator("#btnBuscar")
        if search.count()==1 and search.is_visible() and search.is_enabled():
            search.click(timeout=8000)
            limit=time.monotonic()+15
            while time.monotonic()<limit:
                for candidate in page.locator("table:visible tr").all():
                    cells=candidate.locator("td").all_inner_texts()
                    if len(cells)>=2 and cells[0].strip()==folio and _rut(cells[1].strip().lstrip("0"))==rut and _row_status(candidate):
                        return {**result,"state":"activated","activated":True,"stage":"activation_verified"}
                page.wait_for_timeout(650)
        return result
    except Exception:
        return result


def _work(job_id,rut,folio,activate=False):
    result={"state":"error","activate_available":False,"activated":False}
    stage="configuration"
    started_at=time.monotonic()
    try:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
        required=("DECODO_PROXY_HOST","DECODO_PROXY_PORT","DECODO_PROXY_USER",
                  "DECODO_PROXY_PASSWORD","FONASA_MLE_USER","FONASA_MLE_PASSWORD")
        if any(not os.environ.get(k) for k in required):
            result["state"]="configuration_missing"
            return
        stage="start_chromium"
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
                    stage="open_login"
                    page.goto("https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80",
                              wait_until="domcontentloaded",timeout=18000)
                    if urlparse(page.url).hostname!=_HOST:
                        result["state"]="origin_error";return
                    stage="fill_login"
                    page.locator("#CodUsuario").fill(os.environ["FONASA_MLE_USER"])
                    page.locator("#Password").fill(os.environ["FONASA_MLE_PASSWORD"])
                    stage="send_login"
                    page.locator('button[onclick="validafrmLogin()"]').click(timeout=6500)
                    menu=page.get_by_text(re.compile("Activaci[oó]n y desactivar Cuenta M[eé]dica",re.I))
                    stage="wait_authenticated_menu"
                    menu.first.wait_for(state="visible",timeout=24000)
                    if urlparse(page.url).hostname!=_HOST:
                        result["state"]="origin_error";return
                    stage="open_menu"
                    menu.first.click(timeout=6500)
                    submenu=page.get_by_text(re.compile("Activaci[oó]n Cuenta M[eé]dica",re.I))
                    stage="open_submenu"
                    submenu.first.wait_for(state="visible",timeout=8000)
                    submenu.first.click(timeout=6500)
                    stage="wait_search_form"
                    for name in ("#RutBeneficiario","#FolioBono","#btnBuscar"):
                        page.locator(name).wait_for(state="visible",timeout=10000)
                    stage="fill_search"
                    page.locator("#RutBeneficiario").fill(rut)
                    page.locator("#FolioBono").fill(folio)
                    stage="send_search"
                    page.locator("#btnBuscar").click(timeout=8000)
                    stage="wait_search_results"
                    result["state"]="not_found"
                    result["stage"]=stage
                    result["table_row_seen"]=False
                    result["folio_row_seen"]=False
                    result["rut_row_matched"]=False
                    # An empty/slow page is NOT evidence of an invalid voucher.
                    limit=time.monotonic()+25
                    while time.monotonic()<limit:
                        for row in page.locator("table:visible tr").all():
                            cells=row.locator("td").all_inner_texts()
                            if len(cells)<2:continue
                            result["table_row_seen"]=True
                            if cells[0].strip()!=folio:continue
                            result["folio_row_seen"]=True
                            if _rut(cells[1].strip().lstrip("0"))!=rut:continue
                            result["rut_row_matched"]=True
                            button=row.get_by_role("button",name=re.compile(r"^\s*Activar\s*$",re.I))
                            if _row_status(row):
                                result={"state":"activated","activated":True,"activate_available":False,
                                        "stage":"matched_result_row"}
                            elif button.count()==1 and button.is_visible() and button.is_enabled():
                                if activate:
                                    stage="activation_submitted"
                                    result=_activate_matched(page,row,rut,folio)
                                else:
                                    result={"state":"ready_to_activate","activate_available":True,
                                            "activated":False,"stage":"matched_result_row"}
                            else:
                                result={"state":"found_without_activation_action",
                                        "activate_available":False,"activated":False,"stage":"matched_result_row"}
                            return
                        page.wait_for_timeout(650)
                    if not result["folio_row_seen"]:
                        result["state"]="result_not_confirmed"
                    elif not result["rut_row_matched"]:
                        result["state"]="beneficiary_mismatch"
                finally:
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["state"]="lookup_error"
        result["error_class"]=type(exc).__name__
        result["stage"]=stage
        message=str(exc)
        matched=re.search(r"net::ERR_[A-Z_]+",message)
        if matched:result["network_error"]=matched.group(0)
    finally:
        result["duration_seconds"]=round(time.monotonic()-started_at,1)
        with _lock:
            _jobs[job_id]={"created":time.monotonic(),"data":result}
        # Only operational diagnostics: no RUTs, folios, credentials or page text.
        print("FONASA_BRIDGE_SAFE",{"state":result.get("state"),"stage":result.get("stage",stage),
            "error_class":result.get("error_class"),"network_error":result.get("network_error"),
            "seconds":result["duration_seconds"]},flush=True)

@bp.post("/bridge/fonasa/start")
def start():
    return _start_job(False)


@bp.post("/bridge/fonasa/activate")
def activate_voucher():
    if not _auth():return jsonify(error="unauthorized"),403
    if os.environ.get("PSC_FONASA_ACTIVATION_ENABLED")!="true":
        return jsonify(error="activation_disabled"),503
    return _start_job(True)


def _start_job(activate):
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
    threading.Thread(target=_work,args=(job,rut,folio,activate),daemon=True).start()
    return jsonify(job_id=job,state="running"),202

@bp.get("/bridge/fonasa/status/<job>")
def status(job):
    if not _auth():return jsonify(error="unauthorized"),403
    with _lock:
        item=_jobs.get(job)
        if not item:return jsonify(error="job_unknown_or_expired"),404
        return jsonify(item["data"])
