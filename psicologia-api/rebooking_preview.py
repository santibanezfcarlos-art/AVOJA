"""Patient-facing private rescheduling preview in the isolated test service.

No personal records in URLs or HTML. Disabled unless explicitly enabled.
Changing the calendar is intentionally withheld until transactional safety tests pass.
"""
import os
import re
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, Response
from booking_holds import _database_connection, slot_window, _calendar_free, CATALOG, _active_windows, can_hold, TZ
from rebooking_tokens import check

rebooking_bp = Blueprint("rebooking_preview", __name__)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")

def enabled():
    return os.environ.get("PSICOLOGIA_REBOOKING_PREVIEW_ENABLED") == "true"

def _private_headers(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response

@rebooking_bp.after_request
def headers(response):
    return _private_headers(response)

@rebooking_bp.get("/reagendar")
def page():
    if not enabled():
        return Response("El reagendamiento online todavía no está habilitado.",status=503)
    return Response("""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>Reagendar mi atención · PSC</title>
<style>body{margin:0;font:16px system-ui;background:#f1f7f2;color:#254d39}
main{max-width:560px;margin:8vh auto;padding:30px;background:white;border:1px solid #dce8df;border-radius:18px}
h1{font-size:26px}p{line-height:1.65}label{display:block;margin-top:18px}
input,button{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #b9cfc0;border-radius:9px}
button{margin-top:14px;background:#315e47;color:white;font-weight:bold}
small{color:#526e5e}</style><main><h1>Reagendar mi atención</h1>
<p>Este enlace permite consultar tu reserva y verificar alternativas. Se admite un solo cambio y debe solicitarse con al menos 24 horas de anticipación.</p>
<p id="status" role="status">Verificando enlace privado…</p>
<div id="booking" hidden><p id="summary"></p><label for="day">Nueva fecha</label><input id="day" type="date">
<label for="hour">Nueva hora</label><input id="hour" type="time" step="900">
<button type="button" id="check">Comprobar disponibilidad</button>
<small>La modificación efectiva todavía no está habilitada en esta versión de prueba.</small></div></main>
<script>
const token=new URLSearchParams(location.search).get('t')||'';
history.replaceState(null,'','/reagendar');
const status=document.getElementById('status'), booking=document.getElementById('booking');
async function requestApi(path,opts){
const r=await fetch('/api/reagendar/'+encodeURIComponent(token)+path,{...opts,cache:'no-store',referrerPolicy:'no-referrer'});
return {ok:r.ok,data:await r.json()};}
(async()=>{try{const r=await requestApi('/estado');status.textContent=r.data.message||'No disponible';
if(r.ok){booking.hidden=false;document.getElementById('summary').textContent=r.data.service+' · '+r.data.date+' · '+r.data.time;}}
catch(e){status.textContent='No se pudo verificar el enlace. Intenta nuevamente.'}})();
document.getElementById('check').onclick=async()=>{
const date=document.getElementById('day').value,time=document.getElementById('hour').value;
if(!date||!time)return;status.textContent='Consultando disponibilidad…';
try{const r=await requestApi('/disponibilidad?date='+encodeURIComponent(date)+'&time='+encodeURIComponent(time));
status.textContent=r.data.message||'No disponible';}catch(e){status.textContent='No se pudo consultar la disponibilidad.'}};
</script></html>""",mimetype="text/html")

def _lookup(token):
    if not TOKEN_PATTERN.fullmatch(token):
        return None, "invalid"
    with _database_connection() as conn:
        return check(conn,token)

@rebooking_bp.get("/api/reagendar/<token>/estado")
def status(token):
    if not enabled():
        return jsonify(message="No disponible"),503
    try:
        row, state=_lookup(token)
    except Exception:
        return jsonify(message="No se pudo verificar el enlace"),503
    if state != "eligible":
        return jsonify(message="Este enlace ya no permite reagendar la atención."),410
    return jsonify(message="Puedes consultar nuevos horarios.",service="Atención online",
                   date=row["starts_at"].astimezone(TZ).date().isoformat(),
                   time=row["starts_at"].astimezone(TZ).strftime("%H:%M"))

@rebooking_bp.get("/api/reagendar/<token>/disponibilidad")
def availability(token):
    if not enabled():
        return jsonify(message="No disponible"),503
    try:
        row,state=_lookup(token)
        if state != "eligible":
            return jsonify(message="Este enlace ya no es válido"),410
        with _database_connection() as conn:
            payment=conn.execute("SELECT service FROM psychology_demo_payments WHERE order_id=%s",
                                 (row["order_id"],)).fetchone()
        day,start,end=slot_window(payment["service"],request.args.get("date"),request.args.get("time"))
        if not _calendar_free(start,end):
            return jsonify(available=False,message="Horario ocupado.")
        with _database_connection() as conn:
            windows=_active_windows(conn,day)
        free=can_hold(windows,start,end)
        return jsonify(available=free,message=("Horario disponible para consulta." if free else "Horario no disponible."))
    except Exception:
        return jsonify(message="No se pudo confirmar disponibilidad."),503
