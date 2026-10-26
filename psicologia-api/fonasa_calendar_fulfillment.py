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


@bp.post("/internal/psicologia/fonasa/mail")
def send_fonasa_confirmation():
    """Send existing PSC-branded messages only for a verified Calendar event.

    Reuses the already configured Resend identity of the Webpay pilot;
    independently idempotent per recipient and reservation. No PDF/clinical data.
    """
    body=_authorize()
    if not body or not _enabled():
        return jsonify(state="NOT_AUTHORIZED"),403
    import json
    from html import escape
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    from demo_notifications import _brand
    source=body.get("intake_id","")
    role=body.get("role","")
    recipient=body.get("email","")
    patient=body.get("patient_name","")
    date=body.get("date","")
    hour=body.get("time","")
    minutes=body.get("minutes",45)
    if (not isinstance(source,str) or not re.fullmatch(r"[A-Za-z0-9_-]{15,40}",source)
       or role not in ("patient","professional")
       or not isinstance(recipient,str) or len(recipient)>254
       or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient)
       or not isinstance(patient,str) or not 3<=len(patient)<=120
       or not isinstance(date,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",date)
       or not isinstance(hour,str) or not re.fullmatch(r"(09|1[0-9]):(00|15|30|45)",hour)
       or type(minutes)!=int or minutes not in (45,60,90)):
        return jsonify(state="INVALID"),400
    if role=="professional":
        expected=os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL","").lower()
        trusted={"contacto@pscsantibanez.cl"}
        if expected:
            trusted.add(expected)
        if recipient.lower() not in trusted:
            return jsonify(state="COORDINATOR_NOT_CONFIGURED"),503
    # Each notification must be associated with an existing, verified own event.
    hold_id=uuid.uuid5(FONASA_OWNER,"fonasa:"+source)
    try:
        start=datetime.fromisoformat(date+"T"+hour).replace(tzinfo=TZ)
        end=start+timedelta(minutes=minutes)
        writer=CalendarWriter()
        event=writer.transport("GET",writer._calendar_path+"/"+event_id_for_hold(hold_id),{},None)
        if not _owns(event,hold_id,start=start,end=end,service="psicoterapia"):
            return jsonify(state="CALENDAR_NOT_CONFIRMED"),409
    except (CalendarIntegrationError,ValueError,TypeError,OSError):
        return jsonify(state="CALENDAR_NOT_CONFIRMED"),503
    api_key=os.environ.get("PSICOLOGIA_PILOT_RESEND_API_KEY","")
    if (os.environ.get("PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED")!="true"
       or len(api_key)<24):
        return jsonify(state="MAIL_NOT_CONFIGURED"),503
    from demo_notifications import template as booking_template
    meet=writer.get_meet_link(hold_id)
    subject,html=booking_template(role,source,"psicoterapia",start,end,
        patient_name=patient,meet_url=meet,demonstration=False)
    plain=("Reserva de psicoterapia online CONFIRMADA.\\nPaciente: "+patient+
           "\\nFecha: "+start.strftime("%d/%m/%Y")+"\\nHora Chile: "+
           start.strftime("%H:%M")+"\\nDuración: "+str(minutes)+" minutos"+
           ("\\nGoogle Meet: "+meet if meet else "")+
           "\\nContacto: contacto@pscsantibanez.cl")
    if role=="professional":
        plain+="\\nCorreo de contacto del paciente: "+str(body.get("patient_email",""))
    from_name="Ps. Carlos Santibáñez F. <contacto@pscsantibanez.cl>"
    payload={"from":from_name,"to":[recipient],"subject":subject,"html":html,"text":plain}
    req=Request("https://api.resend.com/emails",method="POST",
        data=json.dumps(payload,ensure_ascii=False).encode("utf-8"),
        headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json",
                 "Idempotency-Key":"psc-fonasa-"+source+"-"+role+"-confirmed-v1"})
    try:
        with urlopen(req,timeout=14) as response:
            parsed=json.loads(response.read(3000))
        if not isinstance(parsed.get("id"),str):
            return jsonify(state="MAIL_UNCONFIRMED"),503
    except HTTPError as exc:
        # Only the HTTP status and provider error code are logged, never addresses,
        # patient data, request bodies or secret keys.
        status=exc.code
        provider_code="unspecified"
        try:
            detail=json.loads(exc.read(1600))
            code=detail.get("name") or detail.get("code")
            if isinstance(code,str) and re.fullmatch(r"[a-z_]{1,50}",code):
                provider_code=code
            description=str(detail.get("message","")).lower()
            if any(term in description for term in ("testing emails","can only send","only send testing","only send emails")):
                provider_code="testing_recipient_restricted"
            elif "verify" in description and ("domain" in description or "sender" in description):
                provider_code="sender_domain_unverified"
            elif "permission" in description or "not authorized" in description:
                provider_code="permission_denied"
            elif "quota" in description or "limit" in description:
                provider_code="quota_restricted"
        except (ValueError,TypeError,OSError):
            pass
        print("FONASA_MAIL_PROVIDER_FAILURE status="+str(status)+" code="+provider_code,flush=True)
        return jsonify(state="MAIL_HTTP_"+str(status)),503
    except (URLError,OSError,ValueError):
        print("FONASA_MAIL_PROVIDER_FAILURE status=network",flush=True)
        return jsonify(state="MAIL_NETWORK_UNAVAILABLE"),503
    return jsonify(state="SENT")


@bp.post("/internal/psicologia/fonasa/invite")
def invite_to_verified_fonasa_booking():
    """Use Google Calendar invite delivery when Resend cannot send email.

    Sends an invitation ONLY for an existing own event and preserves the event,
    its time, Google Meet, and already invited attendees. Idempotent per email.
    """
    body=_authorize()
    if not body or not _enabled():return jsonify(state="NOT_AUTHORIZED"),403
    source=body.get("intake_id","")
    role=body.get("role","")
    recipient=body.get("email","")
    date=body.get("date","")
    hour=body.get("time","")
    minutes=body.get("minutes")
    if not (isinstance(source,str) and re.fullmatch(r"[A-Za-z0-9_-]{15,40}",source)
         and role in ("patient","professional")
         and isinstance(recipient,str) and len(recipient)<=254
         and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",recipient)
         and isinstance(date,str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}",date)
         and isinstance(hour,str) and re.fullmatch(r"(09|1[0-9]):(00|15|30|45)",hour)
         and type(minutes)==int and minutes in (45,60,90)):
        return jsonify(state="INVALID"),400
    if role=="professional":
        trusted={"contacto@pscsantibanez.cl"}
        professional=os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL","").strip().lower()
        if professional:trusted.add(professional)
        if recipient.lower() not in trusted:
            return jsonify(state="NOT_COORDINATOR"),403
    hold_id=uuid.uuid5(FONASA_OWNER,"fonasa:"+source)
    try:
        start=datetime.fromisoformat(date+"T"+hour).replace(tzinfo=TZ)
        end=start+timedelta(minutes=minutes)
        writer=CalendarWriter()
        writer._check(write=True)
        path=writer._calendar_path+"/"+event_id_for_hold(hold_id)
        event=writer.transport("GET",path,{},None)
        if not _owns(event,hold_id,start=start,end=end,service="psicoterapia"):
            return jsonify(state="EVENT_NOT_OWNED"),409
        existing=event.get("attendees") or []
        if not isinstance(existing,list):return jsonify(state="CALENDAR_UNAVAILABLE"),503
        if any(x.get("email","").casefold()==recipient.casefold() for x in existing if isinstance(x,dict)):
            return jsonify(state="ALREADY_INVITED")
        if len(existing)>=18:
            return jsonify(state="TOO_MANY_ATTENDEES"),409
        invitations=[dict(x) for x in existing if isinstance(x,dict)]
        invitations.append({"email":recipient})
        updated=writer.transport("PATCH",path,{"sendUpdates":"all"},
                                 {"attendees":invitations})
        if (not _owns(updated,hold_id,start=start,end=end,service="psicoterapia")
            or not any(x.get("email","").casefold()==recipient.casefold()
                       for x in updated.get("attendees",[]) if isinstance(x,dict))):
            return jsonify(state="INVITE_NOT_VERIFIED"),503
        return jsonify(state="INVITE_ACCEPTED")
    except (CalendarIntegrationError,TypeError,ValueError,OSError):
        return jsonify(state="CALENDAR_UNAVAILABLE"),503
