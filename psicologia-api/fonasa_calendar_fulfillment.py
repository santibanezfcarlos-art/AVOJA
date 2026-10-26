"""Fonasa booking fulfillment using the SAME Google Calendar identity as Webpay.

This route is independently signed server-to-server. It does not pretend that a
Fonasa voucher is a successful Transbank payment. An identical retry is safe.
"""
import re
import uuid
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, jsonify
from public_demo_bridge import _authorize, _enabled
from google_calendar_writer import CalendarWriter, CalendarIntegrationError, _owns, event_id_for_hold

bp=Blueprint("fonasa_calendar_fulfillment",__name__)
TZ=ZoneInfo("America/Santiago")
FONASA_OWNER=uuid.UUID("24c9f09e-1c7f-4b5b-bd3b-65d16f493ce5")

@bp.post("/internal/psicologia/fonasa/confirm")
def confirm_fonasa_booking():
    body=_authorize()
    if not body or not _enabled():
        return jsonify(state="NOT_AUTHORIZED"),403
    source_id=body.get("intake_id","")
    date=body.get("date","")
    hour=body.get("time","")
    patient=body.get("patient_name","")
    if not (isinstance(source_id,str) and re.fullmatch(r"[A-Za-z0-9_-]{15,40}",source_id)
            and isinstance(date,str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}",date)
            and isinstance(hour,str) and re.fullmatch(r"(?:09|1[0-9]):(?:00|15|30|45)",hour)
            and isinstance(patient,str) and 3<=len(patient)<=120):
        return jsonify(state="INVALID"),400
    try:
        start=datetime.fromisoformat(date+"T"+hour).replace(tzinfo=TZ)
        if not datetime.now(TZ)<start<datetime.now(TZ)+timedelta(days=30):
            return jsonify(state="INVALID_DATE"),400
        minutes=int(body.get("minutes",45))
        if minutes not in (45,60,90):
            return jsonify(state="INVALID_DURATION"),400
        end=start+timedelta(minutes=minutes)
        hold_id=uuid.uuid5(FONASA_OWNER,"fonasa:"+source_id)
        writer=CalendarWriter()
        writer._check(write=True)
        event_id=event_id_for_hold(hold_id)
        path=writer._calendar_path+"/"+event_id
        try:
            existing=writer.transport("GET",path,{},None)
        except Exception as exc:
            # Only a 404 means the deterministic event does not already exist.
            from google_calendar_writer import CalendarHttpError
            if not isinstance(exc,CalendarHttpError) or exc.status!=404:
                return jsonify(state="CALENDAR_UNAVAILABLE"),503
            existing=None
        if existing:
            if not _owns(existing,hold_id,start=start,end=end,service="psicoterapia"):
                return jsonify(state="EVENT_CONFLICT"),409
            return jsonify(state="CONFIRMED",event_id=event_id,
                           meet_url=writer.get_meet_link(hold_id))
        if not writer.is_free(start,end):
            return jsonify(state="SLOT_OCCUPIED"),409
        body={"id":event_id,
              "summary":"Atención Fonasa · "+patient,
              "visibility":"private","transparency":"opaque",
              "description":"Reserva de psicoterapia online con bono Fonasa. Sin cobro Transbank.",
              "start":{"dateTime":start.isoformat(),"timeZone":"America/Santiago"},
              "end":{"dateTime":end.isoformat(),"timeZone":"America/Santiago"},
              "extendedProperties":{"private":{
                  "psychology_hold_id":str(hold_id),"psychology_service":"psicoterapia",
                  "psc_booking_source":"fonasa"}}}
        params={"sendUpdates":"none"}
        if os.environ.get("PSICOLOGIA_GOOGLE_MEET_ENABLED")=="true":
            body["conferenceData"]={"createRequest":{
                "requestId":"psc-fonasa-"+uuid.uuid4().hex,
                "conferenceSolutionKey":{"type":"hangoutsMeet"}}}
            params["conferenceDataVersion"]=1
        from google_calendar_writer import CalendarHttpError
        try:
            event=writer.transport("POST",writer._calendar_path,params,body)
        except CalendarHttpError as exc:
            if exc.status!=409:
                return jsonify(state="CALENDAR_UNAVAILABLE"),503
            event=writer.transport("GET",path,{},None)
        if not _owns(event,hold_id,start=start,end=end,service="psicoterapia"):
            return jsonify(state="EVENT_UNVERIFIED"),503
        meet=writer.get_meet_link(hold_id)
        return jsonify(state="CONFIRMED",event_id=event_id,meet_url=meet)
    except (CalendarIntegrationError,OSError,ValueError,TypeError):
        return jsonify(state="CALENDAR_UNAVAILABLE"),503
