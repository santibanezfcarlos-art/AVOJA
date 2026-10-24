"""Patient-facing private rescheduling preview in the isolated test service.

No personal records in URLs or HTML. Disabled unless explicitly enabled.
Changing the calendar is intentionally withheld until transactional safety tests pass.
"""
import os
import re
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, Response
from booking_holds import _database_connection, slot_window, _calendar_free, CATALOG, _active_windows, can_hold, TZ, HoldProblem
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
    return Response("""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Reagendar mi atención · PSC</title><style>
:root{font-family:Arial,sans-serif;color:#194537;background:#f1f7f2}*{box-sizing:border-box}body{margin:0}
header{background:#fff;border-bottom:1px solid #dfe8e1;padding:19px max(4%,calc((100% - 1080px)/2));font-weight:700}
header a{color:#194537;text-decoration:none}main{max-width:1000px;margin:40px auto;padding:0 16px}
.panel{background:white;border:1px solid #dbe7dd;border-radius:18px;padding:32px}
h1{font-size:29px;margin:0 0 12px}p{line-height:1.6;color:#536e61}
.current{background:#f5f9f5;border-radius:12px;padding:16px;margin:20px 0}
.navigation{display:flex;justify-content:space-between;align-items:center;margin:24px 0 14px}
.week{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.day,.slot{border:1px solid #d5e4d9;border-radius:12px;background:white;color:#194537;cursor:pointer;padding:15px 6px;font:inherit}
.day span{display:block;font-size:12px;color:#648271}.day strong{display:block;font-size:21px;margin:5px}
.day.selected,.slot.selected{border-color:#337052;background:#e5f0e8}
.day:disabled,.slot:disabled{opacity:.36;cursor:not-allowed}
.slots{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px}
.slot{padding:22px 8px;font-size:20px;font-weight:bold}.slot small{display:block;font-size:12px;font-weight:400;margin-top:6px}
button.action{background:#315f49;color:#fff;border:0;border-radius:10px;padding:14px 20px;font-size:15px;font-weight:bold;cursor:pointer}
button.action:disabled{background:#9fb7a8;cursor:not-allowed}.navigation button{border:1px solid #d5e4d9;background:#fff;border-radius:9px;padding:9px 14px}
#confirm{margin-top:22px;width:100%}.notice{margin-top:16px;min-height:26px}
.success{background:#edf7ef;border:1px solid #b7d9c2;border-radius:12px;padding:25px;text-align:center}
.home{display:inline-block;margin-top:14px;background:#315f49;color:white;text-decoration:none;padding:13px 22px;border-radius:9px}
[hidden]{display:none!important}@media(max-width:620px){.panel{padding:21px}.week{gap:3px}.day{padding:11px 2px}.day strong{font-size:17px}.slots{grid-template-columns:repeat(2,1fr)}}
</style></head><body><header><a href="https://pscsantibanez.cl/">CS · Carlos Santibáñez Fica</a></header><main><section class="panel">
<h1>Reagendar mi atención</h1><p>Selecciona una nueva fecha y hora. Puedes realizar un único cambio con al menos 24 horas de anticipación, según disponibilidad.</p>
<div id="notice" class="notice" role="status">Verificando tu reserva…</div>
<div id="booking" hidden><div id="current" class="current"></div>
<div class="navigation"><strong id="period">Elige la fecha</strong><div><button type="button" id="prev" aria-label="Semana anterior">←</button> <button type="button" id="next" aria-label="Semana siguiente">→</button></div></div>
<div id="week" class="week"></div><h2 id="dayTitle">Horarios disponibles</h2>
<div id="slots" class="slots"></div>
<button id="confirm" type="button" class="action" disabled>Confirmar cambio de hora</button></div>
<div id="finished" class="success" hidden><h2>¡Tu atención quedó reagendada!</h2>
<p id="confirmedInfo"></p><p>Recibirás una nueva confirmación por correo electrónico.</p>
<a class="home" href="https://pscsantibanez.cl/">Volver a la página principal →</a></div>
</section></main><script>
const token=new URLSearchParams(location.search).get("t")||"";
history.replaceState(null,"","/reagendar");
const el=id=>document.getElementById(id),zone="America/Santiago";
let monday=null,selectedDay=null,selectedHour=null,currentDate=null,currentTime=null,busy=false;
const pad=n=>String(n).padStart(2,"0");
function iso(d){return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate())}
function dateOf(s){return new Date(s+"T12:00:00")}
function add(d,n){const v=new Date(d);v.setDate(v.getDate()+n);return v}
function label(s){return dateOf(s).toLocaleDateString("es-CL",{weekday:"long",day:"numeric",month:"long"})}
function todayChile(){const parts=new Intl.DateTimeFormat("en-CA",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());let o={};parts.forEach(p=>o[p.type]=p.value);return o.year+"-"+o.month+"-"+o.day}
function initialMonday(d){return add(d,-((d.getDay()+6)%7))}
async function api(path,opts={}){const r=await fetch("/api/reagendar/"+encodeURIComponent(token)+path,{...opts,cache:"no-store",referrerPolicy:"no-referrer"});return {ok:r.ok,data:await r.json()}}
function notice(t){el("notice").textContent=t}
let hours=[],loadingHours=false,requestVersion=0;
async function chooseDay(day){
 selectedDay=day;selectedHour=null;hours=[];el("confirm").disabled=true;
 const version=++requestVersion;loadingHours=true;render();notice("Consultando horarios disponibles…");
 try{const r=await api("/horarios?date="+encodeURIComponent(day));
 if(version!==requestVersion)return;
 hours=r.ok&&Array.isArray(r.data.hours)?r.data.hours:[];
 notice(r.ok?(hours.length?"Selecciona una de las horas disponibles.":"No hay horas disponibles para este día."):(r.data.message||"No se pudo verificar la disponibilidad."));
 }catch(e){if(version!==requestVersion)return;hours=[];notice("No se pudo verificar la disponibilidad. Intenta con otra fecha.");}
 finally{if(version===requestVersion){loadingHours=false;render();}}
}
function render(){
const start=iso(monday),limit=iso(add(dateOf(todayChile()),60));
el("period").textContent=dateOf(start).toLocaleDateString("es-CL",{month:"long",year:"numeric"});
el("prev").disabled=iso(add(monday,-7))<iso(initialMonday(dateOf(todayChile())));
el("week").replaceChildren();
for(let i=0;i<7;i++){const d=add(monday,i),day=iso(d),b=document.createElement("button");
b.type="button";b.className="day"+(selectedDay===day?" selected":"");b.disabled=day<todayChile()||day>limit;
const wd=document.createElement("span");wd.textContent=["Do","Lu","Ma","Mi","Ju","Vi","Sa"][d.getDay()];
const num=document.createElement("strong");num.textContent=d.getDate();
b.append(wd,num);b.onclick=()=>chooseDay(day);el("week").append(b)}
el("dayTitle").textContent=selectedDay?label(selectedDay):"Elige un día";
el("slots").replaceChildren();
if(selectedDay){if(loadingHours){el("slots").textContent="Buscando horas libres…";}
else for(const hour of hours){const b=document.createElement("button");b.type="button";
b.className="slot"+(selectedHour===hour?" selected":"");b.textContent=hour;
const small=document.createElement("small");small.textContent="Disponible";b.append(small);
b.onclick=()=>{selectedHour=hour;el("confirm").disabled=false;notice("Hora seleccionada: "+hour+" h.");render()};
el("slots").append(b)}}
}
el("next").onclick=()=>{monday=add(monday,7);selectedDay=null;selectedHour=null;hours=[];requestVersion++;el("confirm").disabled=true;render()};
el("prev").onclick=()=>{monday=add(monday,-7);selectedDay=null;selectedHour=null;hours=[];requestVersion++;el("confirm").disabled=true;render()};
el("confirm").onclick=async()=>{if(!selectedDay||!selectedHour||busy)return;
if(!confirm("¿Confirmas el cambio para "+label(selectedDay)+" a las "+selectedHour+"? Solo puedes reagendar una vez."))return;
busy=true;el("confirm").disabled=true;notice("Confirmando el cambio de hora…");
try{const r=await api("/confirmar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({date:selectedDay,time:selectedHour})});
if(r.ok){el("booking").hidden=true;el("notice").hidden=true;el("finished").hidden=false;
el("confirmedInfo").textContent="Tu reserva quedó para el "+label(r.data.date)+" a las "+r.data.time+" h (hora de Chile).";}
else notice(r.data.message||"No se pudo confirmar el cambio.");}
catch(e){notice("No se pudo confirmar. Contacta al profesional antes de volver a intentarlo.")}
finally{busy=false}};
(async()=>{try{const r=await api("/estado");if(!r.ok){notice(r.data.message||"Enlace no disponible");return}
currentDate=r.data.date;currentTime=r.data.time;
el("current").textContent="Tu reserva actual: "+label(currentDate)+" a las "+currentTime+" h · "+r.data.service;
monday=initialMonday(dateOf(todayChile()));el("booking").hidden=false;notice("Selecciona un nuevo horario.");chooseDay(currentDate);
}catch(e){notice("No se pudo verificar el enlace privado.")}})();
</script></body></html>""",mimetype="text/html")

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



def configured_slots(day_text):
    """Use the same weekly hours, closures and exceptions as the admin panel."""
    import json
    from urllib.request import Request, urlopen
    from datetime import date
    day=date.fromisoformat(day_text)
    req=Request("https://psicologia-carlos-calendar-api.onrender.com/api/booking-config",
                headers={"Accept":"application/json"})
    with urlopen(req,timeout=8) as response:
        config=json.loads(response.read(40000))["settings"]
    weekday=(day.weekday()+1)%7
    exception=config.get("exceptions",{}).get(day_text)
    if exception is not None:
        times=[] if exception.get("closed") else exception["slots"]
    elif weekday in config.get("closedWeekdays",[]):
        times=[]
    else:
        times=config["weekly"][str(weekday)]
    return set(times),int(config.get("maxPerDay",3))

@rebooking_bp.get("/api/reagendar/<token>/horarios")
def available_hours(token):
    if not enabled():
        return jsonify(message="No disponible"),503
    try:
        row,state=_lookup(token)
        if state!="eligible":
            return jsonify(message="Este enlace ya no permite reagendar"),410
        from datetime import date as date_type
        day_text=request.args.get("date","")
        day=date_type.fromisoformat(day_text)
        with _database_connection() as conn:
            payment=conn.execute("SELECT service,hold_id FROM psychology_demo_payments WHERE order_id=%s",
                                 (row["order_id"],)).fetchone()
            if not payment:
                return jsonify(message="Reserva no encontrada"),404
            rows=conn.execute("""SELECT starts_at,ends_at FROM psychology_booking_holds
                  WHERE slot_date=%s AND id<>%s AND (status='CONFIRMED' OR
                  (status IN ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED')
                   AND expires_at>now()))""",(day,payment["hold_id"])).fetchall()
        windows=[(r["starts_at"],r["ends_at"]) for r in rows]
        allowed,maximum=configured_slots(day_text)
        hours=[]
        for hour in sorted(allowed):
            try:
                _,start,end=slot_window(payment["service"],day_text,hour)
            except HoldProblem:
                continue
            if can_hold(windows,start,end,max_daily=maximum) and _calendar_free(start,end):
                hours.append(hour)
        return jsonify(hours=hours,date=day_text)
    except (ValueError,TypeError):
        return jsonify(message="Fecha inválida"),400
    except Exception:
        return jsonify(message="No se pudo comprobar disponibilidad"),503

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
        allowed,maximum=configured_slots(day.isoformat())
        if request.args.get("time") not in allowed:
            return jsonify(available=False,message="Horario no habilitado en la agenda."),200
        if not _calendar_free(start,end):
            return jsonify(available=False,message="Horario ocupado.")
        with _database_connection() as conn:
            windows=_active_windows(conn,day)
        free=can_hold(windows,start,end,max_daily=maximum)
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
            allowed,maximum=configured_slots(day.isoformat())
            if body.get("time") not in allowed:
                return jsonify(message="Este horario no está habilitado."),409
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
            if not can_hold([(w["starts_at"],w["ends_at"]) for w in windows],start,end,max_daily=maximum):
                return jsonify(message="La hora ya no está disponible."),409
            # Calendar writes are allowed only in isolated pilot mode.
            writer=CalendarWriter()
            if not writer.is_free(start,end):
                return jsonify(message="La hora aparece ocupada en Google Calendar."),409
            event_id=writer.reschedule_own_appointment(hold_id=payment["hold_id"],
                service=payment["service"],old_start=old_start,old_end=old_end,
                new_start=start,new_end=end)
            # Database commit may fail even after Calendar accepted the PATCH.
            # Compensate back to the original event if anything fails; never
            # report success unless the database commits.
            try:
                conn.execute("""UPDATE psychology_booking_holds SET starts_at=%s,
                             ends_at=%s,slot_date=%s,updated_at=now() WHERE id=%s""",
                             (start,end,day,payment["hold_id"]))
                conn.execute("""INSERT INTO psychology_reschedule_audit
                             (order_id,old_starts_at,new_starts_at) VALUES (%s,%s,%s)""",
                             (payment["order_id"],old_start,start))
                conn.execute("UPDATE psychology_reschedule_links SET redeemed_at=now() WHERE order_id=%s",
                             (payment["order_id"],))
                conn.commit()
            except Exception:
                conn.rollback()
                try:
                    writer.reschedule_own_appointment(hold_id=payment["hold_id"],
                        service=payment["service"],old_start=start,old_end=end,
                        new_start=old_start,new_end=old_end)
                except Exception:
                    # Calendar and DB may now differ. Require manual reconciliation.
                    # No success or automatic second attempt is advertised.
                    raise RuntimeError("REBOOKING_RECONCILIATION_REQUIRED") from None
                raise RuntimeError("REBOOKING_ROLLED_BACK") from None
        # Los correos se envían después de confirmar la transacción, nunca antes.
        try:
            from demo_notifications import send as mail_send, configured as mail_ready
            if mail_ready():
                with _database_connection() as notify_conn:
                    recipients=notify_conn.execute(
                        "SELECT test_recipient,patient_name FROM psychology_demo_payments WHERE order_id=%s",
                        (payment["order_id"],)).fetchone()
                for role in ("patient","professional"):
                    recipient=(recipients["test_recipient"] if role=="patient"
                               else os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL",""))
                    meeting = None
                    if os.environ.get("PSICOLOGIA_GOOGLE_MEET_ENABLED") == "true":
                        try:
                            meeting = CalendarWriter().get_meet_link(payment["hold_id"])
                        except Exception:
                            pass
                    mail_send(role,payment["order_id"],payment["hold_id"],recipient,
                              payment["service"],start,end,rescheduled=True,
                              patient_name=recipients.get("patient_name"),meet_url=meeting)
        except Exception:
            # Fallo de correo no revierte una reserva ya confirmada.
            pass
        return jsonify(ok=True,message="Tu nueva hora quedó confirmada.",date=day.isoformat(),
                       time=start.strftime("%H:%M"))
    except CalendarIntegrationError:
        return jsonify(message="No se pudo verificar el cambio en Google Calendar."),503
    except Exception:
        return jsonify(message="No se pudo completar el cambio. Contacta al profesional para comprobar tu reserva."),503
