"""Puente firmado de compras SIMULADAS desde la web pscsantibanez.cl.

Recibe operaciones SOLAMENTE de la API Webpay con firma HMAC y reloj acotado.
NO expone la clave de integración a HTML, compradores ni registros.
Separa retención previa, autorización del banco y envío de notificaciones.
"""
import hashlib
import hmac
import json
import os
import re
import uuid
from urllib.request import urlopen
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request
from booking_holds import (
    HoldProblem, _calendar_free, _new_hold, _database_connection,
    _lock_day, slot_window,
)
from demo_checkout import _confirm_calendar, _notify
from demo_notifications import allowed_email, configured as notification_ready

TZ=ZoneInfo("America/Santiago")
public_bridge_bp=Blueprint("psychology_public_demo_bridge",__name__)
ORDER_PATTERN=re.compile(r"PSCD[0-9A-F]{18}\Z")
SESSION_PATTERN=re.compile(r"D2[PEC][0-9]{12}[a-f0-9]{28}\Z")
KEY_NAME="PSICOLOGIA_DEMO_BRIDGE_KEY"


def _enabled():
    return (os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT")=="true"
        and os.environ.get("PSICOLOGIA_HOLDS_CALENDAR_POLICY")=="google_api"
        and os.environ.get("PSICOLOGIA_WEBPAY_MODE","disabled")=="disabled"
        and os.environ.get("PSICOLOGIA_CHECKOUT_MODE","disabled")=="disabled"
        and bool(os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL"))
        and len(os.environ.get(KEY_NAME,""))>=40)


def _authorize():
    if not _enabled():
        return None
    if not request.is_json or (request.content_length or 0)>1800:
        return None
    raw=request.get_data(cache=True)
    ts=request.headers.get("X-Psc-Timestamp","")
    sig=request.headers.get("X-Psc-Signature","")
    if not re.fullmatch(r"[0-9]{10,12}",ts) or not re.fullmatch(r"[0-9a-f]{64}",sig):
        return None
    try:
        now=int(datetime.now(TZ).timestamp())
        if abs(now-int(ts))>120:
            return None
    except ValueError:
        return None
    data_to_sign=ts.encode()+b"."+raw
    expected=hmac.new(os.environ[KEY_NAME].encode(),data_to_sign,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig,expected):
        return None
    data=request.get_json(silent=True)
    return data if isinstance(data,dict) else None


def _reply(state,code=200):
    return jsonify(state=state,simulation=True,real_payment=False),code


def _commercial_service(service):
    """Catálogo leído desde la API de administración, nunca desde el navegador."""
    from booking_holds import CATALOG
    if os.environ.get("PSICOLOGIA_PILOT_USE_SHARED_CATALOG") != "true":
        return {"price": CATALOG[service]["amount_clp"],
                "minutes": CATALOG[service]["minutes"], "active": True}
    try:
        with urlopen("https://psicologia-carlos-calendar-api.onrender.com/api/booking-config",
                     timeout=8) as response:
            document=json.loads(response.read(16000).decode("utf-8"))
        entry=document["settings"]["services"][service]
        price=entry["price"]
        minutes=entry["minutes"]
        active=entry["active"]
        if (type(price) is not int or not 100<=price<=10000000 or
                type(minutes) is not int or minutes not in (45,60,90) or
                type(active) is not bool):
            raise ValueError("invalid catalog")
        return {"price":price,"minutes":minutes,"active":active}
    except Exception:
        return None


@public_bridge_bp.post("/internal/psicologia/demo/preflight")
def preflight():
    data=_authorize()
    if not data:
        return _reply("NOT_AUTHORIZED",403)
    if not notification_ready():
        return _reply("NOTIFICATIONS_DISABLED",503)
    order=data.get("order")
    service=data.get("service")
    session=data.get("session")
    email=data.get("email")
    patient_name=data.get("patient_name")
    if patient_name is not None:
        if (not isinstance(patient_name,str) or not 2<=len(patient_name.strip())<=120
                or any(ord(ch)<32 for ch in patient_name)):
            return _reply("INVALID_NAME",422)
        patient_name=" ".join(patient_name.split())
    # El solicitante debe confirmar que controla el correo utilizado.
    # Solo afecta a transacciones simuladas y no entrega contenido clínico.
    if data.get("email_consent") is not True:
        return _reply("EMAIL_CONSENT_REQUIRED",422)
    from booking_holds import CATALOG
    if service not in CATALOG:
        return _reply("INVALID",400)
    # La API pública ya validó y firmó estos valores con HMAC. No hacemos
    # una segunda petición HTTP a Render: en ocasiones fallaba con 503.
    signed_minutes=data.get("minutes")
    signed_price=data.get("amount")
    if (type(signed_price) is not int or not 100<=signed_price<=10000000
            or type(signed_minutes) is not int or signed_minutes not in (45,60,90)):
        return _reply("INVALID_CATALOG",400)
    selected={"price":signed_price,"minutes":signed_minutes,"active":True}
    if (not isinstance(order,str) or not ORDER_PATTERN.fullmatch(order)
            or not isinstance(session,str) or not SESSION_PATTERN.fullmatch(session)
            or not selected["active"]
            or not isinstance(data.get("amount"),int)
            or data["amount"]!=selected["price"]):
        return _reply("INVALID",400)
    # El propietario puede habilitar correos de ensayo externos. Nunca se
    # envían mensajes sin una compra sandbox aprobada ni fuera del límite.
    if allowed_email(email) is None:
        return _reply("TEST_EMAIL_ONLY",422)
    try:
        day, start, end=slot_window(service,data.get("date"),data.get("time"),duration_minutes=selected["minutes"])
    except HoldProblem as exc:
        return _reply("INVALID_SLOT",exc.status)
    try:
        with _database_connection() as conn:
            known=conn.execute("SELECT state,session_id FROM psychology_demo_payments WHERE order_id=%s",(order,)).fetchone()
        if known:
            return _reply("READY" if known["session_id"]==session else "MISMATCH",
                          200 if known["session_id"]==session else 409)
        # Límites por ventana móvil para impedir abusos del checkout gratuito.
        # Incluye intentos fallidos: evita enviar correos a terceros repetidamente.
        with _database_connection() as conn:
            limit=conn.execute("""SELECT count(*) AS total,
                         count(*) FILTER (WHERE test_recipient=%s) AS recipient
                         FROM psychology_demo_payments
                         WHERE created_at>=now()-interval '24 hours'""",
                         (email.strip().lower(),)).fetchone()
        # Certificación Webpay: admite varias pruebas sucesivas con el mismo correo
        # autorizado. Se mantienen límites de 24 horas contra usos abusivos.
        if limit["total"] >= 48 or limit["recipient"] >= 24:
            return _reply("TEST_LIMIT_REACHED",429)
        try:
            free=_calendar_free(start,end)
        except HoldProblem as exc:
            if exc.status != 503:
                raise
            # Una desconexión temporal de Google no debe crear reservas falsas.
            # Reintentar únicamente la lectura; nunca saltarse la verificación.
            print("PSICOLOGIA_PREFLIGHT: calendar_read_retry",flush=True)
            free=_calendar_free(start,end)
        if not free:
            return _reply("SLOT_UNAVAILABLE",409)
        hold_id,_secret=_new_hold(service,day,start,end)
        with _database_connection() as conn:
            conn.execute("""INSERT INTO psychology_demo_payments
                (order_id,hold_id,session_id,service,amount_clp,test_recipient,patient_name,state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'PAYMENT_PENDING')""",
                (order,uuid.UUID(hold_id),session,service,data["amount"],email.lower(),patient_name))
            conn.execute("""UPDATE psychology_booking_holds
                SET status='PAYMENT_PENDING',expires_at=now()+interval '20 minutes',updated_at=now()
                WHERE id=%s""",(uuid.UUID(hold_id),))
        return _reply("READY")
    except HoldProblem as exc:
        print("PSICOLOGIA_PREFLIGHT: hold_error="+str(exc.status),flush=True)
        return _reply("SLOT_UNAVAILABLE" if exc.status==409 else "UNAVAILABLE",exc.status)
    except Exception as exc:
        print("PSICOLOGIA_PREFLIGHT: error_type="+type(exc).__name__,flush=True)
        return _reply("UNAVAILABLE",503)


@public_bridge_bp.post("/internal/psicologia/demo/complete")
def complete():
    data=_authorize()
    if not data:
        return _reply("NOT_AUTHORIZED",403)
    order=data.get("order")
    session=data.get("session")
    outcome=data.get("outcome")
    if (not isinstance(order,str) or not ORDER_PATTERN.fullmatch(order)
            or outcome not in ("approved","rejected","cancelled")
            or (outcome!="cancelled" and
                (not isinstance(session,str) or not SESSION_PATTERN.fullmatch(session)))
            or not isinstance(data.get("amount"),int)):
        return _reply("INVALID",400)
    try:
        with _database_connection() as conn:
            row=conn.execute("""SELECT p.state,p.session_id,p.amount_clp,p.hold_id,
                    h.slot_date FROM psychology_demo_payments p
                    JOIN psychology_booking_holds h ON p.hold_id=h.id
                    WHERE p.order_id=%s""",(order,)).fetchone()
            if (not row or (outcome!="cancelled" and row["session_id"]!=session)
                    or row["amount_clp"]!=data["amount"]):
                return _reply("MISMATCH",409)
            _lock_day(conn,row["slot_date"])
            row=conn.execute("SELECT state,hold_id FROM psychology_demo_payments WHERE order_id=%s FOR UPDATE",
                             (order,)).fetchone()
            if outcome in ("rejected","cancelled"):
                if row["state"] in ("PAYMENT_PENDING","STARTING"):
                    conn.execute("""UPDATE psychology_demo_payments SET state=%s,updated_at=now()
                                    WHERE order_id=%s""",
                                 ("REJECTED" if outcome=="rejected" else "CANCELLED",order))
                    conn.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=now()
                                    WHERE id=%s AND status IN ('HELD','PAYMENT_PENDING')""",(row["hold_id"],))
                elif row["state"] in ("AUTHORIZED","CALENDAR_PENDING","CONFIRMED"):
                    return _reply("ALREADY_AUTHORIZED",409)
            elif row["state"]=="PAYMENT_PENDING":
                conn.execute("""UPDATE psychology_demo_payments
                    SET state='AUTHORIZED',verified_at=now(),updated_at=now()
                    WHERE order_id=%s""",(order,))
        if outcome=="approved":
            _confirm_calendar(order)
        with _database_connection() as conn:
            updated=conn.execute("SELECT state FROM psychology_demo_payments WHERE order_id=%s",
                                 (order,)).fetchone()
        if not updated:
            return _reply("UNAVAILABLE",503)
        if updated["state"]=="CONFIRMED":
            with _database_connection() as conn:
                sent=conn.execute("""SELECT count(*) AS n FROM psychology_demo_notifications
                                     WHERE order_id=%s AND status='sent'""",(order,)).fetchone()["n"]
            return _reply("CONFIRMED" if sent==2 else "CONFIRMED_EMAIL_PENDING")
        return _reply(updated["state"])
    except Exception:
        return _reply("RECONCILIATION_REQUIRED",503)

@public_bridge_bp.post("/internal/psicologia/demo/busy")
def busy():
    """Signed, privacy-safe snapshot of confirmed/held appointments.

    The public availability API must fail closed if this DB cannot be read.
    Never return names, identifiers, payment details or emails.
    """
    data=_authorize()
    if not data:
        return _reply("NOT_AUTHORIZED",403)
    try:
        from datetime import date
        first=date.fromisoformat(data.get("from",""))
        last=date.fromisoformat(data.get("to",""))
        if last<first or (last-first).days>21:
            return _reply("INVALID_RANGE",400)
        with _database_connection() as conn:
            rows=conn.execute("""SELECT h.starts_at,h.ends_at FROM psychology_booking_holds h
                WHERE h.slot_date BETWEEN %s AND %s
                  AND (h.status='CONFIRMED' OR
                       (h.status IN ('HELD','PAYMENT_PENDING','RECONCILIATION_REQUIRED')
                        AND h.expires_at>now()))""",(first,last)).fetchall()
        # La agenda pública debe aplicar también MAX_DAILY, no solo solapamientos.
        # Cuando ya existen tres reservas/retenciones activas, se bloquea
        # todo el día para evitar ofrecer horarios que el preflight rechaza.
        from collections import Counter
        from datetime import datetime, time, timedelta
        from booking_holds import MAX_DAILY, TZ
        counts = Counter(row["starts_at"].astimezone(TZ).date() for row in rows)
        intervals = [[row["starts_at"].isoformat(), row["ends_at"].isoformat()]
                     for row in rows]
        if data.get("include_calendar") is True:
            # Only signed server callers may request live Google Calendar occupancy.
            # No names, titles, attendee lists, RUTs or event identifiers are returned.
            from datetime import time as day_time
            from google_calendar_writer import CalendarWriter, _parse_edge
            client=CalendarWriter()
            earliest=datetime.combine(first,day_time.min,tzinfo=TZ)
            latest=datetime.combine(last+timedelta(days=1),day_time.min,tzinfo=TZ)
            query={"timeMin":earliest.isoformat(),"timeMax":latest.isoformat(),
                   "singleEvents":"true","showDeleted":"false","maxResults":"2500"}
            cursor=None
            for _ in range(10):
                params=dict(query)
                if cursor:
                    params["pageToken"]=cursor
                response=client.transport("GET",client._calendar_path,params,None)
                if not isinstance(response,dict) or not isinstance(response.get("items"),list):
                    raise ValueError("Incomplete calendar response")
                for event in response["items"]:
                    if event.get("status")=="cancelled" or event.get("transparency")=="transparent":
                        continue
                    start_edge=_parse_edge(event["start"])
                    end_edge=_parse_edge(event["end"])
                    if end_edge>start_edge and start_edge<latest and earliest<end_edge:
                        intervals.append([start_edge.isoformat(),end_edge.isoformat()])
                cursor=response.get("nextPageToken")
                if not cursor:
                    break
                if not isinstance(cursor,str) or len(cursor)>2048:
                    raise ValueError("Invalid calendar pagination")
            else:
                raise ValueError("Google Calendar result limit reached")
        for day, total in counts.items():
            if total >= MAX_DAILY:
                begin = datetime.combine(day, time.min, tzinfo=TZ)
                end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ)
                intervals.append([begin.isoformat(), end.isoformat()])
        return jsonify(state="OK",intervals=intervals)
    except Exception:
        return _reply("UNAVAILABLE",503)

