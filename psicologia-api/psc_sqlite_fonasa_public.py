"""Fonasa patient rebooking adapter for PSC persistent SQLite state.

Only used with both explicit cutover flags. No payment operations here.
Current PostgreSQL booking routes remain untouched by default.
"""
import os,re
from datetime import date,datetime
from flask import jsonify,request
from booking_holds import TZ,slot_window,_calendar_free,can_hold,HoldProblem
from psc_sqlite_fonasa_client import (
    details,destination_busy,prepare_rebooking,commit_rebooking,
    abort_rebooking,StateBridgeUnavailable)
from google_calendar_writer import CalendarWriter,CalendarIntegrationError

TOKEN=re.compile(r"[A-Za-z0-9_-]{32,128}\Z")

def _current(token):
    if not isinstance(token,str) or not TOKEN.fullmatch(token):
        raise ValueError("Invalid link")
    doc=details(token)
    if doc.get("state")!="FOUND" or not isinstance(doc.get("booking"),dict):
        raise ValueError("Link not found")
    row=dict(doc["booking"])
    row["starts_at"]=datetime.fromisoformat(row["starts_at"])
    row["ends_at"]=datetime.fromisoformat(row["ends_at"])
    row["slot_date"]=date.fromisoformat(row["slot_date"])
    return row,doc.get("eligibility")

def _free(row,day_text,hour):
    from rebooking_preview import configured_slots
    day,start,end=slot_window("psicoterapia",day_text,hour)
    allowed,max_daily=configured_slots(day_text)
    if hour not in allowed:return False,start,end
    occupied=destination_busy(day,exclude_hold_id=row["hold_id"])
    if not can_hold(occupied,start,end,max_daily=max_daily):
        return False,start,end
    return _calendar_free(start,end),start,end

def state(token):
    try:
        row,flag=_current(token)
        if flag!="ELIGIBLE":
            return jsonify(message="Este enlace ya no permite reagendar la atención."),410
        return jsonify(message="Puedes consultar nuevos horarios.",
           service="Atención online",date=row["starts_at"].astimezone(TZ).date().isoformat(),
           time=row["starts_at"].astimezone(TZ).strftime("%H:%M"))
    except ValueError:return jsonify(message="Enlace inválido."),410
    except Exception:return jsonify(message="No fue posible verificar tu enlace."),503

def times(token):
    try:
        row,flag=_current(token)
        if flag!="ELIGIBLE":return jsonify(message="Enlace no disponible"),410
        day=date.fromisoformat(request.args.get("date",""))
        from rebooking_preview import configured_slots
        allowed,max_daily=configured_slots(day.isoformat())
        occupied=destination_busy(day,exclude_hold_id=row["hold_id"])
        hours=[]
        for hour in sorted(allowed):
            try:
                _,start,end=slot_window("psicoterapia",day.isoformat(),hour)
                if can_hold(occupied,start,end,max_daily=max_daily) and _calendar_free(start,end):
                    hours.append(hour)
            except HoldProblem:
                continue
        return jsonify(hours=hours,date=day.isoformat())
    except ValueError:return jsonify(message="Fecha inválida."),400
    except Exception:return jsonify(message="No fue posible verificar horarios."),503

def available(token):
    try:
        row,flag=_current(token)
        if flag!="ELIGIBLE":return jsonify(message="Enlace no disponible"),410
        ok,_,_=_free(row,request.args.get("date",""),request.args.get("time",""))
        return jsonify(available=bool(ok),message="Horario disponible." if ok else "Horario ocupado.")
    except ValueError:return jsonify(message="Fecha u hora inválida."),400
    except Exception:return jsonify(message="No se pudo verificar disponibilidad."),503

def confirm(token):
    if os.environ.get("PSC_FONASA_REBOOKING_MIGRATED")!="true":
        return jsonify(message="Reagendamiento no habilitado."),503
    body=request.get_json(silent=True)
    if not isinstance(body,dict) or set(body)!={"date","time"}:
        return jsonify(message="Solicitud inválida."),400
    try:
        row,flag=_current(token)
        if flag!="ELIGIBLE":return jsonify(message="Enlace utilizado o no disponible."),409
        ok,start,end=_free(row,body["date"],body["time"])
        if not ok:return jsonify(message="La nueva hora no está disponible."),409
        old_start,old_end=row["starts_at"],row["ends_at"]
        if start==old_start:return jsonify(message="Selecciona una hora diferente."),400
        writer=CalendarWriter()
        if not writer.own_event_exists(hold_id=row["hold_id"],service="psicoterapia",
                                        start=old_start,end=old_end):
            return jsonify(message="La reserva original no se pudo verificar."),503
        result=prepare_rebooking(row["order_id"],token,start,end)
        if result!="PREPARED":return jsonify(message="La hora ya no está disponible."),409
        # Google move and signed SQLite commit form a recoverable two-phase saga.
        try:
            writer.reschedule_own_appointment(hold_id=row["hold_id"],
                service="psicoterapia",old_start=old_start,old_end=old_end,
                new_start=start,new_end=end)
            if not writer.own_event_exists(hold_id=row["hold_id"],
                    service="psicoterapia",start=start,end=end):
                raise RuntimeError("GOOGLE_EVENT_NOT_VERIFIED")
            committed=commit_rebooking(row["order_id"],token,start,end)
            if committed!="CONFIRMED":
                raise RuntimeError("SQLITE_COMMIT_NOT_VERIFIED")
        except Exception:
            # Only roll back an event if a verified reverse update succeeds.
            try:
                if writer.own_event_exists(hold_id=row["hold_id"],
                        service="psicoterapia",start=start,end=end):
                    writer.reschedule_own_appointment(hold_id=row["hold_id"],
                        service="psicoterapia",old_start=start,old_end=end,
                        new_start=old_start,new_end=old_end)
                if writer.own_event_exists(hold_id=row["hold_id"],
                        service="psicoterapia",start=old_start,end=old_end):
                    abort_rebooking(row["order_id"],token,start,end)
            except Exception:pass
            return jsonify(message="Cambio pendiente de verificación. No vuelvas a reservar; contacta al profesional."),503
        try:
            from fonasa_calendar_fulfillment import notify_rebooked_fonasa
            notify_rebooked_fonasa(row,start,end)
        except Exception:pass
        return jsonify(ok=True,message="Tu nueva hora quedó confirmada.",
                       date=start.astimezone(TZ).date().isoformat(),
                       time=start.astimezone(TZ).strftime("%H:%M"))
    except ValueError:return jsonify(message="Horario inválido."),400
    except Exception:return jsonify(message="No se pudo confirmar la reserva. Contacta al profesional."),503
