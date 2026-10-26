"""PSC Fonasa: private intake and patient booking-link pilot.

Upload supports private administration and consented patient self-service. PDF is parsed in-memory and never retained.
Fonasa verification runs in the isolated signed Frankfurt service.
A booking request is not a confirmed Google Calendar event.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request
import billing
from patient_names import display_name

bp=Blueprint("fonasa_private",__name__)
TZ=ZoneInfo("America/Santiago")
WORKER="https://psc-fonasa-conectividad-frankfurt.onrender.com"
SITE="https://pscsantibanez.cl"
SCHEMA="""
CREATE TABLE IF NOT EXISTS fonasa_intake (
 id TEXT PRIMARY KEY, folio TEXT NOT NULL UNIQUE, rut TEXT NOT NULL,
 patient_name TEXT NOT NULL, emitted TEXT NOT NULL,
 state TEXT NOT NULL, worker_job TEXT,
 token_hash TEXT UNIQUE, token_expiry INTEGER,
 requested_date TEXT, requested_time TEXT, patient_email TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fonasa_intake_state ON fonasa_intake(state);
CREATE TABLE IF NOT EXISTS fonasa_notifications (
  intake_id TEXT NOT NULL, role TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL, last_error TEXT,
  PRIMARY KEY(intake_id,role),
  FOREIGN KEY(intake_id) REFERENCES fonasa_intake(id)
);
"""

def _schema(con):
    con.executescript(SCHEMA)
    columns={row["name"] for row in con.execute("PRAGMA table_info(fonasa_intake)").fetchall()}
    for key,definition in (("activation_status","TEXT NOT NULL DEFAULT 'not_requested'"),
                           ("activation_job","TEXT"),("activation_updated_at","TEXT")):
        if key not in columns:
            con.execute("ALTER TABLE fonasa_intake ADD COLUMN "+key+" "+definition)
    for key in ("link_generated_at", "link_delivered_at", "link_delivery_method",
                "public_access_hash", "public_access_expiry", "public_polled_at",
                "manual_completed_at", "manual_completion_note"):
        if key not in columns:
            con.execute("ALTER TABLE fonasa_intake ADD COLUMN "+key+" TEXT")
    if "check_detail" not in columns:
        con.execute("ALTER TABLE fonasa_intake ADD COLUMN check_detail TEXT")
    if "calendar_event_id" not in columns:
        con.execute("ALTER TABLE fonasa_intake ADD COLUMN calendar_event_id TEXT")
    if "meet_url" not in columns:
        con.execute("ALTER TABLE fonasa_intake ADD COLUMN meet_url TEXT")
    if "calendar_error" not in columns:
        con.execute("ALTER TABLE fonasa_intake ADD COLUMN calendar_error TEXT")

    # One-time historical regularization explicitly requested by the owner.
    # Completion is an audited display override, not invented Calendar/mail evidence.
    con.executescript("""
        UPDATE fonasa_intake SET
          manual_completed_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'),
          manual_completion_note='Regularización manual solicitada por el profesional: atención ya realizada; bono usado en pruebas.'
        WHERE julianday(created_at)<julianday('2026-10-26T09:40:00Z')
          AND manual_completed_at IS NULL
          AND (SELECT count(*) FROM fonasa_intake
               WHERE julianday(created_at)<julianday('2026-10-26T09:40:00Z'))=3;
    """)
    completed=con.execute("SELECT changes()").fetchone()[0]
    if completed:
        import logging
        logging.getLogger("fonasa_history").warning("FONASA_MANUAL_COMPLETION records=%d",completed)

def _lookup_reason(remote):
    state=remote.get("state","")
    stage=remote.get("stage","")
    if state=="configuration_missing":
        return "Faltan credenciales o configuración del proxy en el servidor."
    if state=="lookup_error":
        label={
            "start_chromium":"No pudo iniciarse el navegador remoto.",
            "open_login":"No se pudo abrir el portal de Fonasa.",
            "fill_login":"No se pudieron completar las credenciales.",
            "send_login":"El ingreso al portal no respondió.",
            "wait_authenticated_menu":"No apareció el menú después del inicio de sesión.",
            "open_menu":"No pudo abrirse el menú de Fonasa.",
            "open_submenu":"No apareció el submenú de activación.",
            "wait_search_form":"No cargó el formulario de búsqueda.",
            "fill_search":"No pudieron completarse el RUT y el folio.",
            "send_search":"El botón Buscar no respondió.",
            "wait_search_results":"No terminó de cargar el resultado de búsqueda."
        }.get(stage,"Fonasa no completó la consulta.")
        return label+" ("+str(remote.get("error_class","error"))[:35]+")"
    if state=="result_not_confirmed":
        return "Fonasa no mostró una fila coincidente antes de agotarse el tiempo de espera."
    if state=="beneficiary_mismatch":
        return "El folio apareció, pero el RUT mostrado no coincidió con el beneficiario."
    if state=="found_without_activation_action":
        return "El bono apareció, pero el botón Activar no estaba disponible."
    if state=="not_found":
        return "No se encontró el bono en la respuesta de Fonasa."
    if state=="origin_error":
        return "Fonasa redirigió a un sitio inesperado."
    return "La consulta no pudo confirmarse; el PDF sigue registrado."

def _utc():return datetime.now(TZ).isoformat(timespec="seconds")
def _record(con,id):
    _schema(con)
    return con.execute("SELECT * FROM fonasa_intake WHERE id=?",(id,)).fetchone()

def _rut(value):
    if not isinstance(value,str):return None
    value=re.sub(r"[.\s]","",value).upper()
    m=re.fullmatch(r"(\d{7,8})-([0-9K])",value)
    if not m:return None
    digits,dv=m.groups()
    total=sum(int(v)*(2+i%6) for i,v in enumerate(reversed(digits)))
    r=11-total%11
    return value if dv==("0" if r==11 else "K" if r==10 else str(r)) else None

class VoucherError(ValueError):pass

def parse_tcpdf(data):
    """Conservative parser for the textual Fonasa TCPDF format provided.
    Unsupported/scanned variants fail closed; OCR must be added separately.
    """
    if not (isinstance(data,bytes) and 0<len(data)<=2_000_000 and data.startswith(b"%PDF-")):
        raise VoucherError("Adjunta un PDF válido de hasta 2 MB.")
    items=[]
    for match in re.finditer(rb"stream\r?\n",data):
        stop=data.find(b"endstream",match.end())
        if stop<0:continue
        raw=data[match.end():stop].rstrip(b"\r\n")
        try:
            stream=zlib.decompressobj()
            decoded=stream.decompress(raw,2_000_001)
            if len(decoded)>2_000_000 or stream.unconsumed_tail:raise VoucherError("PDF demasiado complejo.")
        except zlib.error:
            continue
        pattern=rb"BT\s+0 Tr 0\.00 w ET BT\s+([\d.]+)\s+([\d.]+)\s+Td\s+\[\((.*?)\)\]\s+TJ"
        for x,y,part in re.findall(pattern,decoded,re.S):
            value=part.decode("cp1252","replace").replace(r"\(","(").replace(r"\)",")").strip()
            items.append((float(x),float(y),value))
    if not (len(items)>=25 and len(items)<1000 and
            any(value=="FONDO NACIONAL DE SALUD" for _,_,value in items) and
            any(value=="BONO DE ATENCION DE SALUD" for _,_,value in items)):
        raise VoucherError("No se reconoció este formato de bono Fonasa.")
    def right_of(label):
        found=[(x,y) for x,y,t in items if t.upper()==label]
        if len(found)!=1:raise VoucherError("No se encontró un campo obligatorio.")
        x,y=found[0]
        candidates=sorted([(other_x,t) for other_x,other_y,t in items
                          if abs(other_y-y)<0.4 and other_x>x and t.startswith(":")])
        if not candidates:raise VoucherError("Campo obligatorio ilegible.")
        return candidates[0][1].split(":",1)[1].strip()
    folios=[text for _,y,text in items if 760<y<772 and re.fullmatch(r"\d{5,30}",text)]
    if len(folios)!=1:raise VoucherError("Número de bono ilegible.")
    folio=folios[0]
    rut=_rut(right_of("RUT PERSONA BENEFICIARIO(A)"))
    name=right_of("NOMBRE REGISTRAL")
    provider=right_of("PROFESIONAL/INSTITUCION")
    emitted=right_of("FECHA EMISION")
    codes=[text for _,_,text in items if text=="0902002"]
    if not rut or not 4<=len(name)<=120 or not all(c.isprintable() for c in name):
        raise VoucherError("Datos del beneficiario ilegibles.")
    if not provider.startswith("17000344-6 ") or len(codes)!=1:
        raise VoucherError("El bono no corresponde al prestador o prestación.")
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}",emitted):
        raise VoucherError("Fecha de emisión inválida.")
    return {"folio":folio,"rut":rut,"patient_name":name,"emitted":emitted}

def _call_worker(path,body=None,timeout=12):
    key=os.environ.get("PSC_FONASA_BRIDGE_KEY","")
    if len(key)<40:raise RuntimeError("La conexión privada Fonasa no está configurada.")
    raw=None if body is None else json.dumps(body,ensure_ascii=True).encode()
    req=Request(WORKER+path,data=raw,method="POST" if raw is not None else "GET",
                headers={"Authorization":"Bearer "+key,"Content-Type":"application/json",
                         "User-Agent":"PSC-Fonasa-Admin/1.0"})
    try:
        with urlopen(req,timeout=timeout) as resp:
            return json.loads(resp.read(3000).decode())
    except HTTPError as exc:
        raise RuntimeError("Fonasa no aceptó la consulta ("+str(exc.code)+").") from None
    except (URLError,OSError,TimeoutError,ValueError):
        raise RuntimeError("No se pudo contactar el servicio de consulta Fonasa.") from None

def _admin_item(row):
    return {"id":row["id"],"folio":row["folio"],"rut":row["rut"],
            "patient_name":display_name(row["patient_name"]),"emitted":row["emitted"],
            "state":row["state"],"date":row["requested_date"],
            "time":row["requested_time"],"email":row["patient_email"],
            "created_at":row["created_at"],"check_detail":row["check_detail"],
            "calendar_event_id":row["calendar_event_id"],"meet_url":row["meet_url"],
            "calendar_error":row["calendar_error"],
            "activation_status":row["activation_status"],
            "manual_completed_at":row["manual_completed_at"],
            "manual_completion_note":row["manual_completion_note"],
            "link_generated":bool(row["token_hash"]),
            "link_generated_at":row["link_generated_at"],
            "link_delivered_at":row["link_delivered_at"],
            "link_delivery_method":row["link_delivery_method"]}


def _notification_sender():
    """Report the shared PSC bridge rather than unused local mail credentials."""
    from webpay_demo_bridge import enabled
    return ("psc_webpay_bridge", None) if enabled() else (None, None)


def _send_notification(role,recipient,row):
    """Corporate email uses the same verified-event bridge and template as Webpay.

    A Calendar invitation is never an acknowledgement from the email provider.
    """
    from webpay_demo_bridge import fonasa_confirmation_email, BridgeError
    from admin import settings
    cfg,_=settings()
    minutes=cfg["services"]["psicoterapia"]["minutes"]
    try:
        state=fonasa_confirmation_email(
            intake_id=row["id"],role=role,email=recipient,
            patient_name=display_name(row["patient_name"]),day=row["requested_date"],
            hour=row["requested_time"],minutes=minutes,
            patient_email=row["patient_email"])
    except BridgeError as exc:
        raise RuntimeError("mail_delivery_"+exc.state[:48]) from None
    if state!="SENT":
        raise RuntimeError("mail_status_"+state[:48])
    # The corporate patient email carries the calendar attachment. Do not send
    # a second native Google invitation from the personal calendar owner.


def _deliver_pending_notifications(intake_id):
    """Try both recipients independently; persist status so admin can retry failures."""
    outcome={}
    for role in ("professional","patient"):
        with billing.db() as con:
            _schema(con)
            row=_record(con,intake_id)
            if not row or row["state"]!="calendar_confirmed" or not row["patient_email"]:
                return {"state":"not_eligible"}
            email=(os.environ.get("PSICOLOGIA_FONASA_COORDINATION_EMAIL") or
                   "santibanezf.carlos@gmail.com") if role=="professional" else row["patient_email"]
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email):
                outcome[role]="invalid_recipient";continue
            con.execute("""INSERT OR IGNORE INTO fonasa_notifications
                        (intake_id,role,status,updated_at) VALUES(?,?,'pending',?)""",
                        (intake_id,role,_utc()))
            previous=con.execute("""SELECT status,attempts FROM fonasa_notifications
                                   WHERE intake_id=? AND role=?""",(intake_id,role)).fetchone()
            if previous["status"]=="sent":
                outcome[role]="sent";continue
            if previous["status"]=="sending":
                outcome[role]="in_progress";continue
            if previous["attempts"]>=5:
                outcome[role]="needs_manual_review";continue
            con.execute("""UPDATE fonasa_notifications SET status='sending',attempts=attempts+1,
                           updated_at=? WHERE intake_id=? AND role=?""",(_utc(),intake_id,role))
        try:
            _send_notification(role,email,row)
            state="sent";error=None
        except RuntimeError as exc:
            state="failed";error=str(exc)[:85]
        with billing.db() as con:
            con.execute("""UPDATE fonasa_notifications SET status=?,last_error=?,updated_at=?
                           WHERE intake_id=? AND role=?""",
                        (state,error,_utc(),intake_id,role))
        outcome[role]=state
    return outcome


# Log configuration readiness only; never log SMTP passwords, API keys or recipients.
try:
    import logging
    logging.getLogger("fonasa_mail").warning(
        "FONASA_MAIL_READY configured=%s provider=%s",
        _notification_sender()[0] is not None,
        _notification_sender()[0] or "missing")
except Exception:
    pass


@bp.get("/admin/fonasa/mail-status")
@billing.private
def mail_status():
    transport,_=_notification_sender()
    return jsonify(mail_configured=transport is not None,
                   mail_transport=transport or "not_configured",
                   coordinator_address_configured=bool(
                       os.environ.get("PSICOLOGIA_FONASA_COORDINATION_EMAIL")))


@bp.get("/admin/fonasa/mail-drafts/<id>")
@billing.private
def coordination_mail_drafts(id):
    """Admin-only mailto fallback when no server email credentials are configured."""
    with billing.db() as con:
        row=_record(con,id)
        if not row:
            return jsonify(error="Solicitud no encontrada."),404
        if row["state"]!="calendar_confirmed" or not row["patient_email"]:
            return jsonify(error="No hay una solicitud de hora registrada."),409
        recipient=row["patient_email"]
        coordinator=(os.environ.get("PSICOLOGIA_FONASA_COORDINATION_EMAIL")
                     or "contacto@pscsantibanez.cl")
        when=(row["requested_date"]+" "+row["requested_time"]+" (Chile)")
        professional=("Nueva solicitud Fonasa pendiente de confirmación\\n\\n"
                      "Paciente: "+row["patient_name"]+"\\n"
                      "Fecha y hora: "+when+"\\n"
                      "Correo de contacto: "+recipient+"\\n\\n"
                      "La cita quedó CONFIRMADA en Google Calendar.")
        patient=("Hola, "+row["patient_name"]+".\\n\\n"
                 "Recibimos tu solicitud de psicoterapia online para "+when+".\\n"
                 "La cita está PENDIENTE de confirmación en Google Calendar. "
                 "Nos comunicaremos contigo para confirmar.\\n\\n"
                 "PSC Santibáñez")
    return jsonify(
        professional={"to":coordinator,"subject":"Reserva Fonasa confirmada · coordinación",
                      "body":professional.replace("\\\\n","\\n")},
        patient={"to":recipient,"subject":"Tu solicitud de hora Fonasa | PSC Santibáñez",
                 "body":patient.replace("\\\\n","\\n")},
        status="draft_only_not_sent")


def _confirm_fonasa_calendar(intake_id):
    """Reconcile a real Fonasa booking using the same Google identity as Webpay.

    No Transbank outcome is synthesized. The pilot uses a deterministic event
    ID so retrying after a lost response cannot create a second event.
    """
    from webpay_demo_bridge import confirm_fonasa_booking, BridgeError
    with billing.db() as con:
        row=_record(con,intake_id)
        if not row or row["state"] not in ("calendar_pending","appointment_requested","calendar_confirmed"):
            return {"state":"not_eligible"}
        if row["state"]=="calendar_confirmed":
            return {"state":"calendar_confirmed","meet_url":row["meet_url"]}
        starts=row["requested_date"]
        clock=row["requested_time"]
        if row["activation_status"]!="activated":
            return {"state":"activation_pending","meet_url":None}
        name=display_name(row["patient_name"])
        from admin import settings
        cfg,_=settings()
        minutes=cfg["services"]["psicoterapia"]["minutes"]
    try:
        result=confirm_fonasa_booking(intake_id=intake_id,day=starts,hour=clock,
                                      patient_name=name,minutes=minutes)
    except BridgeError as exc:
        with billing.db() as con:
            con.execute("""UPDATE fonasa_intake SET state='calendar_pending',
                calendar_error=?,updated_at=? WHERE id=? AND state IN ('calendar_pending','appointment_requested')""",
                (exc.state[:70],_utc(),intake_id))
        return {"state":"calendar_pending","reason":exc.state}
    with billing.db() as con:
        con.execute("""UPDATE fonasa_intake SET state='calendar_confirmed',
            calendar_event_id=?,meet_url=?,calendar_error=NULL,updated_at=?
            WHERE id=? AND state IN ('calendar_pending','appointment_requested')""",
            (result["event_id"],result["meet_url"],_utc(),intake_id))
    return {"state":"calendar_confirmed","meet_url":result["meet_url"]}


@bp.post("/admin/fonasa/confirm/<id>")
@billing.private
def reconcile_fonasa_booking(id):
    """Admin-only retry for prior or in-flight Fonasa appointment requests."""
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Solicitud no encontrada."),404
        if row["state"] not in ("appointment_requested","calendar_pending","calendar_confirmed"):
            return jsonify(error="El bono aún no tiene solicitud de hora."),409
    result=_confirm_fonasa_calendar(id)
    if result["state"]=="calendar_confirmed":
        result["notifications"]=_deliver_pending_notifications(id)
    return jsonify(result)


@bp.post("/admin/fonasa/notify/<id>")
@billing.private
def notify_saved_request(id):
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Solicitud no encontrada."),404
        if row["state"]!="calendar_confirmed":
            return jsonify(error="La cita todavía no está confirmada en Google Calendar."),409
    return jsonify(notifications=_deliver_pending_notifications(id))


@bp.post("/admin/fonasa/upload")
@billing.private
def upload():
    return _upload()

def _upload():
    uploaded=request.files.get("bono")
    if not uploaded:return jsonify(error="Selecciona un archivo PDF."),400
    if not uploaded.filename.lower().endswith(".pdf"):return jsonify(error="Solo PDF."),400
    data=uploaded.stream.read(2_000_001)
    try:info=parse_tcpdf(data)
    except VoucherError as exc:return jsonify(error=str(exc)),400
    id=secrets.token_urlsafe(16)
    try:
        with billing.db() as con:
            _schema(con)
            con.execute("""INSERT INTO fonasa_intake
                (id,folio,rut,patient_name,emitted,state,created_at,updated_at)
                VALUES(?,?,?,?,?,'pdf_read',?,?)""",
                (id,info["folio"],info["rut"],info["patient_name"],
                 info["emitted"],_utc(),_utc()))
    except sqlite3.IntegrityError:
        with billing.db() as con:
            _schema(con)
            existing=con.execute("SELECT * FROM fonasa_intake WHERE folio=?",(info["folio"],)).fetchone()
        if existing and existing["rut"]==info["rut"]:
            return jsonify(ok=True,existing=True,bono={**_admin_item(existing),"emitted":existing["emitted"]},state=existing["state"])
        return jsonify(error="El folio ya existe con datos distintos. Revisa el historial."),409
    return jsonify(ok=True,bono={"id":id,**info},state="pdf_read"),201

@bp.get("/admin/fonasa/items")
@billing.private
def items():
    with billing.db() as con:
        _schema(con)
        rows=con.execute("SELECT * FROM fonasa_intake ORDER BY created_at DESC LIMIT 50").fetchall()
    return jsonify(items=[_admin_item(r) for r in rows])

@bp.post("/admin/fonasa/check/<id>")
@billing.private
def start_lookup(id):
    return _start_lookup(id)

def _start_lookup(id):
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Bono no encontrado."),404
        if row["state"] not in ("pdf_read","check_failed"):
            return jsonify(error="La consulta ya fue iniciada o completada."),409
        con.execute("BEGIN IMMEDIATE")
        claim=con.execute("UPDATE fonasa_intake SET state='checking',worker_job=NULL,updated_at=? WHERE id=? AND state IN ('pdf_read','check_failed')",(_utc(),id))
        if claim.rowcount!=1:return jsonify(state="checking",id=id),202
    try:
        result=_call_worker("/bridge/fonasa/start",{"rut":row["rut"],"folio":row["folio"]})
        if result.get("state")!="running" or not isinstance(result.get("job_id"),str):
            raise RuntimeError("El servicio de consulta no aceptó el trabajo.")
    except RuntimeError as exc:
        with billing.db() as con:
            con.execute("UPDATE fonasa_intake SET state='check_failed',check_detail=?,updated_at=? WHERE id=? AND state='checking'",(str(exc),_utc(),id))
        return jsonify(error=str(exc)),503
    with billing.db() as con:
        con.execute("UPDATE fonasa_intake SET worker_job=?,check_detail=NULL,updated_at=? WHERE id=? AND state='checking'",
                    (result["job_id"],_utc(),id))
    return jsonify(state="checking",id=id),202

@bp.get("/admin/fonasa/status/<id>")
@billing.private
def lookup_status(id):
    return _lookup_status(id)

def _lookup_status(id):
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Bono no encontrado."),404
        state=row["state"]
        activated=row["activation_status"]=="activated"
        detail=row["check_detail"]
        if state=="checking":
            if not row["worker_job"]:
                age=(datetime.now(TZ)-datetime.fromisoformat(row["updated_at"])).total_seconds()
                if age<150:return jsonify(id=id,state="checking")
                con.execute("UPDATE fonasa_intake SET state='check_failed',check_detail=? WHERE id=?",("La consulta no pudo iniciarse. Puedes volver a intentarla.",id))
                return jsonify(id=id,state="check_failed")
            try:remote=_call_worker("/bridge/fonasa/status/"+row["worker_job"])
            except RuntimeError as exc:
                # A cold restart can discard jobs kept only in worker memory.
                # After a bounded time, unlock a safe manual retry.
                age=(datetime.now(TZ)-datetime.fromisoformat(row["updated_at"])).total_seconds()
                if age<150:
                    return jsonify(error=str(exc),state="checking"),503
                state="check_failed"
                detail="La consulta expiró o se reinició el servicio. Puedes volver a intentarla."
            else:
                if remote.get("state")=="activated" and remote.get("activated") is True:
                    state="ready_to_activate"
                    detail=None
                    activated=True
                    con.execute("UPDATE fonasa_intake SET activation_status='activated',activation_updated_at=? WHERE id=?",(_utc(),id))
                elif remote.get("state")=="ready_to_activate" and remote.get("activate_available") is True:
                    state="ready_to_activate"
                    detail=None
                elif remote.get("state")!="running":
                    state="check_failed"
                    detail=_lookup_reason(remote)
            if state!=row["state"]:
                con.execute("UPDATE fonasa_intake SET state=?,check_detail=?,updated_at=? WHERE id=?",
                           (state,detail,_utc(),id))
    return jsonify(id=id,state=state,activate_available=state=="ready_to_activate" and not activated,
                   activated=activated,reason=detail)

@bp.post("/admin/fonasa/link/<id>")
@billing.private
def issue_booking_link(id):
    return _issue_booking_link(id)

def _issue_booking_link(id, token=None):
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Bono no encontrado."),404
        if row["state"] not in ("ready_to_activate","link_issued"):
            return jsonify(error="Primero debe aparecer Activar en Fonasa."),409
        if row["activation_status"]!="activated":
            return jsonify(error="Primero activa el bono y espera la confirmación de Fonasa."),409
        token=token or secrets.token_urlsafe(32)
        token_hash=hashlib.sha256(token.encode()).hexdigest()
        expiry=int(time.time())+7*86400
        con.execute("""UPDATE fonasa_intake SET token_hash=?,token_expiry=?,
                       state='link_issued',link_generated_at=?,link_delivered_at=NULL,
                       link_delivery_method=NULL,updated_at=? WHERE id=?""",
                    (token_hash,expiry,_utc(),_utc(),id))
    return jsonify(link=SITE+"/fonasa-reserva.html#"+token,expires_in_days=7)

def _by_token(con,token):
    if not isinstance(token,str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,80}",token):
        return None
    digest=hashlib.sha256(token.encode()).hexdigest()
    row=con.execute("SELECT * FROM fonasa_intake WHERE token_hash=?",(digest,)).fetchone()
    if not row or not row["token_expiry"] or row["token_expiry"]<int(time.time()):
        return None
    if row["state"] not in ("link_issued","appointment_requested","calendar_pending","calendar_confirmed"):return None
    if row["state"]=="link_issued" and row["activation_status"]!="activated":return None
    return row

@bp.get("/api/fonasa/book/<token>")
def booking_info(token):
    with billing.db() as con:
        _schema(con);row=_by_token(con,token)
    if row is None:return jsonify(error="Este enlace no es válido o venció."),404
    return jsonify(name=display_name(row["patient_name"]),state=row["state"],
                   booked_date=row["requested_date"],booked_time=row["requested_time"],
                   service="Psicoterapia individual online",duration=45,
                   meet_url=row["meet_url"] if row["state"]=="calendar_confirmed" else None,
                   timezone="America/Santiago")

class AvailabilityUnavailable(RuntimeError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def _slots(con,row,days=20,with_schedule=False):
    """Fail closed against calendar, bridge and own pending reservations."""
    from main import _load_busy_intervals, CalendarRefreshingError, CalendarConfigurationError
    from admin import settings, allowed_slot
    from webpay_demo_bridge import enabled, busy_intervals, BridgeError
    today=datetime.now(TZ).date()
    last=today+timedelta(days=days)
    try:
        config,_=settings()
    except (sqlite3.Error,RuntimeError,ValueError,KeyError):
        raise AvailabilityUnavailable("settings_unavailable") from None
    try:
        calendar_intervals=_load_busy_intervals()
    except (CalendarRefreshingError,CalendarConfigurationError):
        raise AvailabilityUnavailable("calendar_refreshing") from None
    except (OSError,ValueError,TypeError,RuntimeError):
        raise AvailabilityUnavailable("calendar_error") from None
    if calendar_intervals is None:
        raise AvailabilityUnavailable("calendar_missing")
    busy=list(calendar_intervals)
    if enabled():
        try:
            busy+=busy_intervals(today,last,include_calendar=True)
        except (BridgeError,ValueError,TypeError,RuntimeError):
            raise AvailabilityUnavailable("booking_bridge_unavailable") from None
    # Pending requests in this module block the same period.
    others=con.execute("""SELECT requested_date,requested_time
         FROM fonasa_intake WHERE state IN ('appointment_requested','calendar_pending','calendar_confirmed')
         AND requested_date>=? AND requested_date<=?""",
         (today.isoformat(),last.isoformat())).fetchall()
    from datetime import datetime as DT
    for item in others:
        start=DT.fromisoformat(item["requested_date"]+"T"+item["requested_time"]).replace(tzinfo=TZ)
        busy.append((start,start+timedelta(minutes=45)))
    # Use exactly the service configuration that powers the normal PSC agenda.
    service=config["services"]["psicoterapia"]
    duration=service["minutes"]
    if not service["active"]:
        raise AvailabilityUnavailable("service_unavailable")
    available={}
    scheduled={}
    now_local=datetime.now(TZ)
    for delta in range(days+1):
        day=today+timedelta(days=delta)
        date_str=day.isoformat()
        same_day=sum(1 for o in others if o["requested_date"]==date_str)
        times=[]
        offered=[]
        for mins in range(9*60,20*60-duration+1,15):
            hour=f"{mins//60:02d}:{mins%60:02d}"
            if not allowed_slot(config,"psicoterapia",date_str,hour):
                continue
            start=DT(day.year,day.month,day.day,mins//60,mins%60,tzinfo=TZ)
            end=start+timedelta(minutes=duration)
            if start<=now_local:
                continue
            offered.append(hour)
            if same_day>=config["maxPerDay"]:
                continue
            if any(start<other_end and other_start<end for other_start,other_end in busy):
                continue
            times.append(hour)
        scheduled[date_str]=offered
        if times:
            available[date_str]=times
    if with_schedule:
        return {"available":available,"scheduled":scheduled,"minutes":duration,
                "range_start":today.isoformat(),"range_end":last.isoformat(),
                "maximum_daily":config["maxPerDay"]}
    return available

@bp.get("/api/fonasa/book/<token>/availability")
def booking_availability(token):
    with billing.db() as con:
        _schema(con);row=_by_token(con,token)
        if not row:return jsonify(error="Enlace inválido o vencido."),404
        if row["state"] in ("appointment_requested","calendar_pending","calendar_confirmed"):
            return jsonify(available={},already_requested=True)
        try:
            view=_slots(con,row,with_schedule=True)
        except AvailabilityUnavailable as exc:
            import logging
            logging.getLogger("fonasa_availability").warning("FONASA_AVAILABILITY_DIAGNOSTIC stage=%s",exc.code)
            return jsonify(error="No se pudo confirmar disponibilidad. Intenta nuevamente.",reason=exc.code),503
        except Exception as exc:
            import logging
            logging.getLogger("fonasa_availability").warning("FONASA_AVAILABILITY_DIAGNOSTIC stage=unexpected kind=%s",type(exc).__name__)
            return jsonify(error="No se pudo confirmar disponibilidad. Intenta nuevamente.",reason="unexpected"),503
    return jsonify(**view,timezone="America/Santiago")

@bp.post("/api/fonasa/book/<token>")
def request_booking(token):
    body=request.get_json(silent=True)
    if not isinstance(body,dict) or (request.content_length or 0)>2500:
        return jsonify(error="Solicitud inválida."),400
    day=body.get("date");hour=body.get("time");email=body.get("email")
    if (not isinstance(day,str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",day)
       or not isinstance(hour,str) or not re.fullmatch(r"(09|1[0-9]):(00|15|30|45)",hour)
       or not isinstance(email,str) or len(email)>254
       or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email)
       or body.get("email_is_mine") is not True):
        return jsonify(error="Selecciona una hora e indica un correo propio válido."),400
    with billing.db() as con:
        _schema(con)
        row=_by_token(con,token)
        if row is None:return jsonify(error="Enlace inválido o vencido."),404
        if row["state"]!="link_issued":
            return jsonify(error="Este bono ya tiene una solicitud registrada."),409
        try:slots=_slots(con,row)
        except Exception:return jsonify(error="No se pudo verificar la agenda. No se guardó la solicitud."),503
        if hour not in slots.get(day,[]):
            return jsonify(error="Ese horario ya no está disponible."),409
        # Recheck our own reservations atomically, without network I/O under SQLite write lock.
        con.execute("BEGIN IMMEDIATE")
        row=_by_token(con,token)
        if not row or row["state"]!="link_issued":
            return jsonify(error="La solicitud ya cambió de estado."),409
        existing=con.execute("""SELECT requested_time FROM fonasa_intake
            WHERE state IN ('appointment_requested','calendar_pending','calendar_confirmed')
            AND requested_date=?""",(day,)).fetchall()
        chosen_mins=int(hour[:2])*60+int(hour[3:])
        for record in existing:
            other=record["requested_time"]
            minute=int(other[:2])*60+int(other[3:])
            if chosen_mins<minute+45 and minute<chosen_mins+45:
                return jsonify(error="Ese horario acaba de ocuparse."),409
        from admin import settings
        config,_=settings()
        if len(existing)>=config["maxPerDay"]:
            return jsonify(error="Ya no hay cupos para esa fecha."),409
        con.execute("""UPDATE fonasa_intake SET state='calendar_pending',
            requested_date=?,requested_time=?,patient_email=?,updated_at=?
            WHERE id=? AND state='link_issued'""",(day,hour,email.strip().lower(),_utc(),row["id"]))
    outcome=_confirm_fonasa_calendar(row["id"])
    if outcome.get("state")=="calendar_confirmed":
        notices=_deliver_pending_notifications(row["id"])
        return jsonify(ok=True,status="calendar_confirmed",
           message="Reserva confirmada en Google Calendar.",
           meet_url=outcome.get("meet_url"),notifications=notices,date=day,time=hour),201
    return jsonify(ok=True,status="calendar_pending",
        message="Recibimos tu solicitud. La confirmación en Google Calendar está pendiente.",
        date=day,time=hour),202

@bp.get("/admin/fonasa")
@billing.private
def admin_page():
    from flask import send_file
    return send_file(Path(__file__).parent/"fonasa_admin.html")

@bp.get("/admin/fonasa-ui.js")
@billing.private
def admin_js():
    from flask import send_file
    return send_file(Path(__file__).parent/"fonasa_admin.js")

@bp.after_request
def private_cache_headers(response):
    response.headers["Cache-Control"]="no-store, private"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["X-Content-Type-Options"]="nosniff"
    return response


@bp.post("/internal/fonasa/rescheduled")
def sync_rescheduled_booking():
    """Signed mirror update from the shared rescheduling engine; no Calendar write."""
    key=os.environ.get("PSICOLOGIA_DEMO_BRIDGE_KEY", "")
    raw=request.get_data(cache=True)
    stamp=request.headers.get("X-Psc-Timestamp", "")
    signature=request.headers.get("X-Psc-Signature", "")
    if (len(key)<40 or len(raw)>1800 or not re.fullmatch(r"[0-9]{10,12}",stamp)
        or abs(time.time()-int(stamp))>120 or not re.fullmatch(r"[a-f0-9]{64}",signature)
        or not hmac.compare_digest(signature,hmac.new(key.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest())):
        return jsonify(state="NOT_AUTHORIZED"),403
    data=request.get_json(silent=True)
    if not isinstance(data,dict):return jsonify(state="INVALID"),400
    try:
        old=datetime.fromisoformat(data["old_start"])
        new=datetime.fromisoformat(data["new_start"])
        if old.tzinfo is None or new.tzinfo is None:
            return jsonify(state="INVALID"),400
        old,new=old.astimezone(TZ),new.astimezone(TZ)
        with billing.db() as con:
            row=_record(con,data["intake_id"])
            if not row or row["state"]!="calendar_confirmed" or row["calendar_event_id"]!=data["event_id"]:
                return jsonify(state="BOOKING_NOT_CONFIRMED"),409
            current=(row["requested_date"],row["requested_time"])
            target=(new.date().isoformat(),new.strftime("%H:%M"))
            if current==target:return jsonify(state="SYNCED")
            if current!=(old.date().isoformat(),old.strftime("%H:%M")):
                return jsonify(state="BOOKING_CHANGED"),409
            con.execute("UPDATE fonasa_intake SET requested_date=?,requested_time=?,updated_at=? WHERE id=?",(*target,_utc(),row["id"]))
        return jsonify(state="SYNCED")
    except (KeyError,ValueError,TypeError):
        return jsonify(state="INVALID"),400


@bp.post("/admin/fonasa/activate/<id>")
@billing.private
def request_bono_activation(id):
    return _request_bono_activation(id)

def _request_bono_activation(id):
    """The operator selects the voucher; existing bookings are never bulk activated."""
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Bono no encontrado."),404
        if row["state"] not in ("ready_to_activate","link_issued"):
            return jsonify(error="Primero verifica el bono en Fonasa. La activación ocurre antes del agendamiento."),409
        previous=row["activation_status"]
        if previous in ("activated","running"):
            return jsonify(activation_status=previous)
        # An uncertain result is reconciled through LOOKUP, without another click.
        verify_only=previous=="needs_review"
        claim=con.execute("""UPDATE fonasa_intake SET activation_status='running',
            activation_job=NULL,activation_updated_at=? WHERE id=? AND activation_status=?""",(_utc(),id,previous))
        if claim.rowcount!=1:return jsonify(activation_status="running")
    path="/bridge/fonasa/start" if verify_only else "/bridge/fonasa/activate"
    try:
        result=_call_worker(path,{"rut":row["rut"],"folio":row["folio"]})
        job=result.get("job_id")
        if not isinstance(job,str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,60}",job):
            raise RuntimeError("Worker did not acknowledge")
    except RuntimeError:
        with billing.db() as con:
            con.execute("UPDATE fonasa_intake SET activation_status='needs_review',activation_updated_at=? WHERE id=?",(_utc(),id))
        return jsonify(error="No se pudo confirmar la solicitud. Usa Revisar activación para consultar el estado sin volver a activar."),503
    with billing.db() as con:
        con.execute("UPDATE fonasa_intake SET activation_job=?,activation_updated_at=? WHERE id=?",(job,_utc(),id))
    return jsonify(activation_status="running")


@bp.get("/admin/fonasa/activation/<id>")
@billing.private
def bono_activation_status(id):
    return _bono_activation_status(id)

def _bono_activation_status(id):
    with billing.db() as con:
        row=_record(con,id)
        if not row:return jsonify(error="Bono no encontrado."),404
        state=row["activation_status"]
        if state!="running":return jsonify(activation_status=state)
        age=(datetime.now(TZ)-datetime.fromisoformat(row["activation_updated_at"])).total_seconds() if row["activation_updated_at"] else 0
        if not row["activation_job"]:
            if age<150:return jsonify(activation_status="running")
            con.execute("UPDATE fonasa_intake SET activation_status='needs_review' WHERE id=?",(id,))
            return jsonify(activation_status="needs_review")
        try:remote=_call_worker("/bridge/fonasa/status/"+row["activation_job"])
        except RuntimeError:
            remote={"state":"unavailable"}
        if remote.get("state")=="running" and age<180:return jsonify(activation_status="running")
        if remote.get("state")=="activated" and remote.get("activated") is True:
            state="activated"
        elif remote.get("state")=="ready_to_activate" and remote.get("activated") is False:
            state="not_requested"
        else:
            state="needs_review"
        con.execute("UPDATE fonasa_intake SET activation_status=?,activation_updated_at=? WHERE id=?",(state,_utc(),id))
    return jsonify(activation_status=state)



@bp.post("/admin/fonasa/link-delivery/<id>")
@billing.private
def record_link_delivery(id):
    """Operator attests actual sending; generating/copying never implies delivery."""
    with billing.db() as con:
        row=_record(con,id)
        if not row or not row["token_hash"]:
            return jsonify(error="Primero genera el enlace privado."),409
        con.execute("UPDATE fonasa_intake SET link_delivered_at=?,link_delivery_method='manual' WHERE id=?",(_utc(),id))
    return jsonify(ok=True)
