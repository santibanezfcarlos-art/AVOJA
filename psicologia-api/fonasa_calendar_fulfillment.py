"""Fonasa booking fulfillment using the SAME Google Calendar identity as Webpay.

This route is independently signed server-to-server. It does not pretend that a
Fonasa voucher is a successful Transbank payment. An identical retry is safe.
"""
import re
import uuid
import os
from datetime import datetime, timedelta
from patient_names import display_name
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
    patient=display_name(body.get("patient_name",""))
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
              "description":"Reserva de psicoterapia online con bono Fonasa.",
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
    source=body.get("intake_id","")
    role=body.get("role","")
    recipient=body.get("email","")
    patient=display_name(body.get("patient_name",""))
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
        expected=os.environ.get("PSICOLOGIA_FONASA_PRO_EMAIL",
                                os.environ.get("PSICOLOGIA_PILOT_PRO_EMAIL", "santibanezf.carlos@gmail.com")).strip().lower()
        trusted={"santibanezf.carlos@gmail.com"}
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
    from demo_notifications import NotificationError, safe_error_label
    from fonasa_mail import send as corporate_send
    from booking_holds import _database_connection
    from fonasa_rebooking import register
    patient_email=recipient if role=="patient" else body.get("patient_email","")
    if not isinstance(patient_email,str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",patient_email):
        return jsonify(state="INVALID_PATIENT_EMAIL"),400
    try:
        with _database_connection() as conn:
            token=register(conn,source,hold_id,patient,patient_email,start,end)
        link="https://psicologia-retenciones-piloto.onrender.com/reagendar?t="+token if role=="patient" else None
        if body.get("prepare_only") is True:
            if body.get("refresh_presentation") is True:
                updated=writer.transport("PATCH",writer._calendar_path+"/"+event_id_for_hold(hold_id),
                    {"sendUpdates":"none"},{"summary":"Atención Fonasa · "+patient,
                     "description":"Reserva de psicoterapia online con bono Fonasa."})
                if not _owns(updated,hold_id,start=start,end=end,service="psicoterapia"):
                    return jsonify(state="EVENT_UNVERIFIED"),503
            return jsonify(state="PREPARED",rebooking_url=link)
        meet=writer.get_meet_link(hold_id)
        corporate_send(role,source,recipient,patient,start,end,event,meet=meet,rebooking_url=link)
    except NotificationError as exc:
        print("FONASA_MAIL_PROVIDER_FAILURE "+safe_error_label(exc),flush=True)
        status=exc.http_status
        return jsonify(state="MAIL_HTTP_"+str(status) if status else "MAIL_NETWORK_UNAVAILABLE"),503
    except Exception:
        print("FONASA_MAIL_PREPARATION_FAILURE",flush=True)
        return jsonify(state="MAIL_PREPARATION_UNAVAILABLE"),503
    print("FONASA_MAIL_PROVIDER_ACCEPTED role="+role,flush=True)
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
        fonasa_pro=os.environ.get("PSICOLOGIA_FONASA_PRO_EMAIL","").strip().lower()
        if fonasa_pro:trusted.add(fonasa_pro)
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


@bp.post('/internal/psicologia/fonasa/mail-diagnostic')
def mail_diagnostic():
    """Signed, non-sending probes. Empty payloads cannot deliver any email.

    Compare the legacy client with the shared client, without disclosing keys,
    recipients, provider bodies, account identifiers or clinical information.
    """
    if not _authorize() or not _enabled():
        return jsonify(state='NOT_AUTHORIZED'),403
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    key=os.environ.get('PSICOLOGIA_PILOT_RESEND_API_KEY','')
    probes={}
    for client in ('legacy','shared'):
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
        if client=='shared':
            headers.update({'Accept':'application/json','User-Agent':'pscsantibanez-reservas/1.0'})
        req=Request('https://api.resend.com/emails',method='POST',data=b'{}',headers=headers)
        try:
            with urlopen(req,timeout=6) as response:
                probes[client]={'status':response.status,'category':'unexpected_ack'}
        except HTTPError as exc:
            raw=exc.read(2000)
            category='non_json_response'
            try:
                doc=json.loads(raw)
                code=doc.get('name') or doc.get('code')
                if isinstance(code,str) and re.fullmatch(r'[a-z_]{1,48}',code):
                    category=code
            except (ValueError,AttributeError):
                if b'1010' in raw:
                    category='upstream_1010'
                elif 'html' in exc.headers.get('Content-Type','').lower():
                    category='upstream_html_block'
            probes[client]={'status':exc.code,'category':category}
        except (URLError,OSError):
            probes[client]={'category':'network_unavailable'}
    # List metadata only when this key allows it; sending-only keys may deny it.
    metadata={}
    for resource in ('domains','api-keys'):
        req=Request('https://api.resend.com/'+resource,headers={
            'Authorization':'Bearer '+key,'User-Agent':'pscsantibanez-reservas/1.0','Accept':'application/json'})
        try:
            with urlopen(req,timeout=6) as response:
                doc=json.loads(response.read(30000))
            items=doc.get('data',[])
            if resource=='domains':
                metadata['psc_domain_status']=[x.get('status') for x in items if x.get('name')=='pscsantibanez.cl']
            else:
                # A key cannot be matched to its name unless the provider exposes
                # that identity; do not infer account ownership from other keys.
                metadata['key_metadata_accessible']=isinstance(items,list)
        except HTTPError as exc:
            metadata[resource+'_http_status']=exc.code
        except (URLError,OSError,ValueError,AttributeError):
            metadata[resource+'_unavailable']=True
    print('FONASA_MAIL_DIAGNOSTIC '+json.dumps({'probes':probes,'metadata':metadata}),flush=True)
    return jsonify(state='DIAGNOSTIC_COMPLETE',probes=probes,metadata=metadata)


def notify_rebooked_fonasa(booking, start, end):
    """Same shared engine, real Fonasa wording and corporate calendar copy."""
    from fonasa_mail import send
    try:
        writer=CalendarWriter()
        event=writer.transport("GET",writer._calendar_path+"/"+event_id_for_hold(booking["hold_id"]),{},None)
        meet=writer.get_meet_link(booking["hold_id"])
    except Exception:
        print("FONASA_REBOOKING_MAIL_PENDING reason=calendar_read",flush=True)
        return
    sync_fonasa_intake(booking,event,start)
    for role in ("patient","professional"):
        recipient=booking["test_recipient"] if role=="patient" else "santibanezf.carlos@gmail.com"
        try:
            send(role,booking["order_id"],recipient,booking["patient_name"],start,end,event,meet=meet,rescheduled=True)
            print("FONASA_REBOOKING_MAIL_ACCEPTED role="+role,flush=True)
        except Exception:
            print("FONASA_REBOOKING_MAIL_PENDING role="+role,flush=True)


def sync_fonasa_intake(booking,event,start):
    import hashlib,hmac,json,time
    from urllib.request import Request,urlopen
    raw=json.dumps({"intake_id":booking["order_id"],"old_start":booking["starts_at"].isoformat(),
                    "new_start":start.isoformat(),"event_id":event["id"]},separators=(",",":")).encode()
    stamp=str(int(time.time()))
    signature=hmac.new(os.environ["PSICOLOGIA_DEMO_BRIDGE_KEY"].encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
    req=Request("https://psicologia-carlos-calendar-api.onrender.com/internal/fonasa/rescheduled",data=raw,method="POST",
                headers={"Content-Type":"application/json","X-Psc-Timestamp":stamp,"X-Psc-Signature":signature,
                         "User-Agent":"pscsantibanez-reservas/1.0"})
    try:
        with urlopen(req,timeout=12) as response:
            assert json.loads(response.read(1000)).get("state")=="SYNCED"
        print("FONASA_REBOOKING_INTAKE_SYNCED",flush=True)
    except Exception:
        print("FONASA_REBOOKING_INTAKE_SYNC_PENDING",flush=True)


@bp.post('/internal/psicologia/fonasa/rebooking-prepare')
def prepare_fonasa_rebooking():
    """Non-sending preparation is exposed separately from mail delivery."""
    body=_authorize()
    if not body or body.get('prepare_only') is not True:
        return jsonify(state='NOT_AUTHORIZED'),403
    return send_fonasa_confirmation()
