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
<button type="button" id="confirm" disabled>Confirmar cambio de hora</button>
<small>El cambio solo se confirma cuando el servidor verifica Google Calendar.</small></div></main>
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
status.textContent=r.data.message||'No disponible';
document.getElementById('confirm').disabled=!r.data.available;
}catch(e){status.textContent='No se pudo consultar la disponibilidad.'}}; 
document.getElementById('confirm').onclick=async()=>{
 if(!window.confirm('¿Confirmas tu única reprogramación? No podrás modificar nuevamente esta hora.'))return;
 const button=document.getElementById('confirm');button.disabled=true;status.textContent='Confirmando nuevo horario…';
 try{const r=await requestApi('/confirmar',{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify({date:document.getElementById('day').value,time:document.getElementById('hour').value})});
 status.textContent=r.data.message||'No se pudo confirmar.';
 if(r.ok){booking.hidden=true;}
 }catch(e){status.textContent='No se pudo confirmar. Contacta al profesional antes de volver a intentarlo.'}
};
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

@rebooking_bp.post("/api/reagendar/<token>/confirmar")
def confirm(token):
    """One-use operation. Never acknowledges a change unless Google verifies it."""
    if not enabled() or os.environ.get("PSICOLOGIA_REBOOKING_CONFIRM_ENABLED") != "true":
        return jsonify(message="La confirmación todavía no está habilitada."),503
    if not TOKEN_PATTERN.fullmatch(token):
        return jsonify(message="Enlace inválido"),404
    body=request.get_json(silent=True) or {}
    try:
        from google_calendar_writer import CalendarWriter, CalendarIntegrationError
        from booking_holds import _lock_day, _expire
        from rebooking_tokens import digest
        with _database_connection() as conn:
            # Serialise this link and the destination slot within one transaction.
            link=conn.execute("SELECT order_id FROM psychology_reschedule_links WHERE token_sha256=%s FOR UPDATE",
                              (digest(token),)).fetchone()
            if not link:
                return jsonify(message="Enlace inválido"),404
            payment=conn.execute("""SELECT p.order_id,p.state,p.service,p.hold_id,
                               h.slot_date,h.starts_at,h.ends_at
                               FROM psychology_demo_payments p JOIN psychology_booking_holds h ON h.id=p.hold_id
                               WHERE p.order_id=%s FOR UPDATE OF p,h""",
                               (link["order_id"],)).fetchone()
            checked,state=check(conn,token)
            if state!="eligible" or payment["state"]!="CONFIRMED":
                return jsonify(message="La reprogramación no está disponible."),409
            day,start,end=slot_window(payment["service"],body.get("date"),body.get("time"))
            old_start,old_end=payment["starts_at"],payment["ends_at"]
            if start==old_start:
                return jsonify(message="Elige un horario diferente."),400
            # Locks both day rows in stable order to avoid concurrent deadlocks.
            for d in sorted({payment["slot_date"],day}):
                _lock_day(conn,d)
            _expire(conn,day)
            windows=conn.execute("""SELECT starts_at,ends_at FROM psychology_booking_holds
                       WHERE slot_date=%s AND id<>%s AND
                       (status='CONFIRMED' OR (status IN
                       ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED') AND expires_at>now()))""",
                       (day,payment["hold_id"])).fetchall()
            if not can_hold([(w["starts_at"],w["ends_at"]) for w in windows],start,end):
                return jsonify(message="La hora ya no está disponible."),409
            # Calendar writes are allowed only in isolated pilot mode.
            writer=CalendarWriter()
            if not writer.is_free(start,end):
                return jsonify(message="La hora aparece ocupada en Google Calendar."),409
            event_id=writer.reschedule_own_appointment(hold_id=payment["hold_id"],
                service=payment["service"],old_start=old_start,old_end=old_end,
                new_start=start,new_end=end)
            # If a database write fails here, the server reports an error, not success.
            # A reconciliation task is required for rare Calendar/DB split failures.
            conn.execute("""UPDATE psychology_booking_holds SET starts_at=%s,
                         ends_at=%s,slot_date=%s,updated_at=now() WHERE id=%s""",
                         (start,end,day,payment["hold_id"]))
            conn.execute("""INSERT INTO psychology_reschedule_audit
                         (order_id,old_starts_at,new_starts_at) VALUES (%s,%s,%s)""",
                         (payment["order_id"],old_start,start))
            conn.execute("UPDATE psychology_reschedule_links SET redeemed_at=now() WHERE order_id=%s",
                         (payment["order_id"],))
        # Los correos se envían después de confirmar la transacción, nunca antes.
        try:
            from demo_notifications import send as mail_send, configured as mail_ready
            if mail_ready():
                with _database_connection() as notify_conn:
                    recipients=notify_conn.execute(
                        "SELECT test_recipient FROM psychology_demo_payments WHERE order_id=%s",
                        (payment["order_id"],)).fetchone()
                for role in ("patient","professional"):
                    recipient=(recipients["test_recipient"] if role=="patient"
                               else os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL",""))
                    mail_send(role,payment["order_id"],payment["hold_id"],recipient,
                              payment["service"],start,end,rescheduled=True)
        except Exception:
            # Fallo de correo no revierte una reserva ya confirmada.
            pass
        return jsonify(ok=True,message="Tu nueva hora quedó confirmada.",date=day.isoformat(),
                       time=start.strftime("%H:%M"))
    except CalendarIntegrationError:
        return jsonify(message="No se pudo verificar el cambio en Google Calendar."),503
    except Exception:
        return jsonify(message="No se pudo completar el cambio. Contacta al profesional para comprobar tu reserva."),503
