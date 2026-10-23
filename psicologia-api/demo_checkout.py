"""Recorrido aislado: Webpay integración → PostgreSQL → Calendar → correos.

NO es una pasarela pública; exige código privado de ensayo para iniciar
compras y solo acepta un correo de prueba configurado en Render.
Nunca llama a APIs productivas ni crea boletas. Transbank verifica el pago
en el servidor antes de confirmar el calendario.
"""
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
from html import escape
import os
import secrets
import uuid
from urllib.parse import urlsplit

from flask import Blueprint, Response, redirect, request, url_for

from booking_holds import (
    HoldProblem, _calendar_free, _cancel_hold, _database_connection,
    _lock_day, _new_hold, slot_window, TZ,
)
from demo_notifications import allowed_email, configured as mail_configured, send as mail_send, NotificationError
from google_calendar_writer import CalendarWriter, CalendarIntegrationError
from webpay_gateway import (
    Gateway, GatewaySettings, GatewayError, AMOUNTS, is_authorized
)

demo_bp = Blueprint("psychology_checkout_trial", __name__)
TRIAL_SITE = "https://psicologia-retenciones-piloto.onrender.com"


def pilot_enabled():
    return (os.environ.get("PSICOLOGIA_PILOT_WEBPAY_TEST_ENABLED") == "true"
            and os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") == "true"
            and os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") == "disabled"
            and os.environ.get("PSICOLOGIA_CHECKOUT_MODE", "disabled") == "disabled"
            and bool(os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL"))
            and os.environ.get("PSICOLOGIA_HOLDS_CALENDAR_POLICY") == "google_api"
            and os.environ.get("PSICOLOGIA_API_PUBLIC_URL", "").rstrip("/") == TRIAL_SITE
            and bool(allowed_email(os.environ.get("PSICOLOGIA_PILOT_TEST_EMAIL")))
            and len(os.environ.get("PSICOLOGIA_DEMO_ACCESS_CODE", "")) >= 24)


def gateway():
    # Credenciales oficiales del ambiente de integración; prohibida producción.
    settings=GatewaySettings.from_environment(mode="integration")
    # El retorno debe ir al checkout de ensayo, no a la API productiva.
    settings=GatewaySettings(settings.environment,settings.commerce_code,settings.api_key,
                             settings.host,TRIAL_SITE + "/demo/return")
    return Gateway(settings=settings)


def _page(title, content, status=200):
    page = ("""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>""" + escape(title) + """ | PSC Integración</title>
<style>body{background:#f4f7f3;color:#163d33;font:16px/1.55 system-ui,sans-serif;margin:0;padding:4rem 1rem}
main{background:#fff;border:1px solid #dce7df;border-radius:18px;max-width:690px;margin:auto;padding:30px}
h1{font-size:2rem;line-height:1.18}label{display:block;margin-top:16px;font-weight:600}input,select{font:inherit;border-radius:10px;border:1px solid #a5baa9;padding:12px;box-sizing:border-box;width:100%}
button,.button{cursor:pointer;background:#184b39;color:#fff;border:0;border-radius:10px;display:inline-block;padding:13px 22px;font:inherit;font-weight:650;margin-top:22px;text-decoration:none}
.note{background:#fff1df;border-radius:10px;padding:12px}small{color:#587369}a{color:#14513e}</style>
</head><body><main><p><b>Ps. Carlos Santibáñez F.</b> · Integración de reservas</p>
<p class="note"><b>SOLO PRUEBAS:</b> Transbank no realiza cobros reales. 
No utilizar datos de pacientes, ni boletas ni agenda clínica real.</p>
<h1>""" + escape(title) + "</h1>" + content + "</main></body></html>")
    resp=Response(page,status=status,mimetype="text/html")
    resp.headers["Cache-Control"]="no-store"
    resp.headers["X-Robots-Tag"]="noindex, nofollow"
    resp.headers["X-Frame-Options"]="DENY"
    resp.headers["Referrer-Policy"]="no-referrer"
    resp.headers["Content-Security-Policy"]="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
    return resp


def _error(message,status=409):
    return _page("No se pudo continuar", "<p>" + escape(message) + "</p><a href='/demo'>Volver a la prueba</a>", status)


@demo_bp.get("/demo")
def index():
    if not pilot_enabled():
        return _error("El ensayo completo no está habilitado en este servidor.",503)
    email=escape(os.environ["PSICOLOGIA_PILOT_TEST_EMAIL"],quote=True)
    return _page("Compra de prueba con Webpay Plus",
        "<p>Selecciona un servicio, fecha y hora libres. Se retendrá por hasta 20 minutos mientras se completa el proceso de pago de prueba.</p>"
        "<form method='post' action='/demo/start'>"
        "<label>Servicio</label><select name='service'>"
        "<option value='psicoterapia'>Psicoterapia breve — $30.000</option>"
        "<option value='empleabilidad'>Asesoría en empleabilidad — $45.000</option>"
        "<option value='competencias'>Potencia tus competencias — $30.000</option></select>"
        "<label>Fecha</label><input name='date' type='date' required>"
        "<label>Hora (cada 15 minutos, entre 09:00 y 20:00)</label>"
        "<input name='time' type='time' min='09:00' max='19:15' step='900' required>"
        "<label>Correo de pruebas (no editable)</label>"
        "<input type='email' value='" + email + "' readonly>"
        "<label>Código privado de acceso a la prueba</label>"
        "<input type='password' name='access_code' autocomplete='off' required>"
        "<button type='submit'>Ir al formulario de pruebas de Transbank</button>"
        "</form><p><small>Al aprobarse una compra simulada, solo se intenta crear un evento marcado como PRUEBA si Google permite escritura. "
        "Los correos solo se envían si el proveedor está configurado. Ninguna de estas reservas corresponde a una atención real.</small></p>")


def _token_digest(token):
    return sha256(token.encode()).hexdigest()


def _auto_submit(token,checkout_url):
    # Únicamente el endpoint de Transbank previamente validado por Gateway.
    content = ("<p>Abriendo el formulario oficial de integración…</p>"
               "<form id='tbk' method='post' action='" + escape(checkout_url,quote=True) + "'>"
               "<input type='hidden' name='token_ws' value='" + escape(token,quote=True) + "'>"
               "<noscript><button type='submit'>Continuar a Webpay Plus de prueba</button></noscript>"
               "</form><script>document.getElementById('tbk').submit();</script>")
    page=_page("Pago de prueba iniciado",content)
    page.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "form-action https://webpay3gint.transbank.cl; base-uri 'none'")
    return page


@demo_bp.post("/demo/start")
def start():
    if not pilot_enabled():
        return _error("Ensayo deshabilitado",503)
    if (request.content_length or 0) > 1024:
        return _error("Solicitud demasiado grande",400)
    origin=request.headers.get("Origin","")
    if origin and origin != TRIAL_SITE:
        return _error("Origen no permitido",403)
    provided=request.form.get("access_code","")
    expected=os.environ["PSICOLOGIA_DEMO_ACCESS_CODE"]
    if not isinstance(provided,str) or not hmac.compare_digest(provided,expected):
        return _error("Código de acceso de prueba incorrecto",403)
    service=request.form.get("service","")
    try:
        day,starts,ends=slot_window(service,request.form.get("date",""),request.form.get("time",""))
        if not _calendar_free(starts,ends):
            return _error("La hora está ocupada en tu Google Calendar.",409)
        hold_id,secret=_new_hold(service,day,starts,ends)
    except HoldProblem as exc:
        return _error(str(exc),exc.status)

    order="PSI"+secrets.token_hex(8).upper()
    session="PSIS"+secrets.token_hex(12).upper()
    email=allowed_email(os.environ.get("PSICOLOGIA_PILOT_TEST_EMAIL"))
    try:
        with _database_connection() as conn:
            conn.execute("""INSERT INTO psychology_demo_payments
                   (order_id,hold_id,session_id,service,amount_clp,test_recipient)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                   (order,uuid.UUID(hold_id),session,service,AMOUNTS[service],email))
        checkout=gateway().create_for_existing_hold(order=order,session_id=session,service=service)
        with _database_connection() as conn:
            conn.execute("""UPDATE psychology_demo_payments SET
                            state='PAYMENT_PENDING',token_sha256=%s,updated_at=now()
                            WHERE order_id=%s AND state='STARTING'""",
                         (_token_digest(checkout["token"]),order))
            conn.execute("""UPDATE psychology_booking_holds SET
                            status='PAYMENT_PENDING',expires_at=now()+interval '20 minutes',
                            updated_at=now() WHERE id=%s AND status='HELD'""",(uuid.UUID(hold_id),))
    except Exception:
        try:
            _cancel_hold(hold_id,secret)
        except Exception:
            # Falla de cierre: la retención vence automáticamente.
            pass
        return _error("No pudimos iniciar el pago de prueba. La hora será liberada.",503)
    return _auto_submit(checkout["token"],checkout["url"])


def _mark_cancelled(conn,order):
    conn.execute("""UPDATE psychology_demo_payments SET state='CANCELLED',updated_at=now()
                   WHERE order_id=%s AND state IN ('STARTING','PAYMENT_PENDING','COMMITTING')""",(order,))
    conn.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=now()
                    WHERE id=(SELECT hold_id FROM psychology_demo_payments WHERE order_id=%s)
                    AND status IN ('HELD','PAYMENT_PENDING')""",(order,))


def _mark_rejected(conn,order):
    conn.execute("""UPDATE psychology_demo_payments SET state='REJECTED',updated_at=now()
                    WHERE order_id=%s AND state IN ('PAYMENT_PENDING','COMMITTING')""",(order,))
    conn.execute("""UPDATE psychology_booking_holds SET status='CANCELLED',updated_at=now()
                    WHERE id=(SELECT hold_id FROM psychology_demo_payments WHERE order_id=%s)
                    AND status IN ('HELD','PAYMENT_PENDING')""",(order,))


def _confirm_calendar(order):
    """Post-commit: idempotente por ID de evento y comprobación en BD."""
    with _database_connection() as conn:
        payment=conn.execute("""SELECT p.*,h.slot_date,h.starts_at,h.ends_at,h.expires_at,h.status AS hold_status
                                FROM psychology_demo_payments p JOIN psychology_booking_holds h
                                ON h.id=p.hold_id WHERE order_id=%s""",(order,)).fetchone()
    if not payment or payment["state"] not in ("AUTHORIZED","CALENDAR_PENDING","CONFIRMED"):
        return
    if payment["state"]=="CONFIRMED":
        _notify(order)
        return
    with _database_connection() as conn:
        _lock_day(conn,payment["slot_date"])
        held=conn.execute("SELECT status,expires_at FROM psychology_booking_holds WHERE id=%s FOR UPDATE",
                          (payment["hold_id"],)).fetchone()
        if not held or held["status"]!="PAYMENT_PENDING" or held["expires_at"]<=datetime.now(TZ):
            conn.execute("UPDATE psychology_demo_payments SET state='RECONCILIATION_REQUIRED',updated_at=now() WHERE order_id=%s",(order,))
            return
    writer=CalendarWriter()
    if not writer.settings.write_enabled:
        with _database_connection() as conn:
            conn.execute("UPDATE psychology_demo_payments SET state='CALENDAR_PENDING',updated_at=now() WHERE order_id=%s",(order,))
        return
    try:
        if not writer.is_free(payment["starts_at"],payment["ends_at"]):
            # Si un intento anterior creó el evento y perdió el ACK, tratarlo
            # como idempotente solo cuando ID/horario/servicio coinciden.
            if not writer.own_event_exists(
                    hold_id=payment["hold_id"],service=payment["service"],
                    start=payment["starts_at"],end=payment["ends_at"]):
                raise CalendarIntegrationError("La hora ya figura ocupada en Google")
        event=writer.create_paid_appointment(
            hold_id=payment["hold_id"],service=payment["service"],start=payment["starts_at"],
            end=payment["ends_at"],payment_verified=True,demo_only=True)
    except CalendarIntegrationError:
        with _database_connection() as conn:
            conn.execute("""UPDATE psychology_demo_payments
                            SET state='RECONCILIATION_REQUIRED',updated_at=now() WHERE order_id=%s""",(order,))
            conn.execute("""UPDATE psychology_booking_holds
                            SET status='RECONCILIATION_REQUIRED',
                            expires_at=now()+interval '1 day',updated_at=now() WHERE id=%s
                            AND status='PAYMENT_PENDING'""",(payment["hold_id"],))
        return
    with _database_connection() as conn:
        _lock_day(conn,payment["slot_date"])
        conn.execute("""UPDATE psychology_demo_payments
                        SET state='CONFIRMED',calendar_event_id=%s,updated_at=now()
                        WHERE order_id=%s AND state IN ('AUTHORIZED','CALENDAR_PENDING')""",
                     (event,order))
        conn.execute("""UPDATE psychology_booking_holds SET status='CONFIRMED',updated_at=now()
                        WHERE id=%s AND status='PAYMENT_PENDING'""",(payment["hold_id"],))
        for role in ("professional","patient"):
            conn.execute("""INSERT INTO psychology_demo_notifications(order_id,recipient_role)
                            VALUES (%s,%s) ON CONFLICT DO NOTHING""",(order,role))
    _notify(order)


def _notify(order):
    if not mail_configured():
        return
    with _database_connection() as conn:
        data=conn.execute("""SELECT p.*,h.starts_at,h.ends_at FROM psychology_demo_payments p
                     JOIN psychology_booking_holds h ON p.hold_id=h.id
                     WHERE p.order_id=%s AND p.state='CONFIRMED'""",(order,)).fetchone()
    if not data:
        return
    for role in ("professional","patient"):
        with _database_connection() as conn:
            claim=conn.execute("""UPDATE psychology_demo_notifications
                                  SET status='sending',attempted_at=now()
                                  WHERE order_id=%s AND recipient_role=%s AND status IN ('pending','failed')
                                  RETURNING order_id""",(order,role)).fetchone()
        if not claim:
            continue
        try:
            mail_send(role,order,data["hold_id"],data["test_recipient"],data["service"],
                      data["starts_at"],data["ends_at"])
            with _database_connection() as conn:
                conn.execute("""UPDATE psychology_demo_notifications
                    SET status='sent',sent_at=now() WHERE order_id=%s AND recipient_role=%s""",
                    (order,role))
        except NotificationError:
            with _database_connection() as conn:
                conn.execute("""UPDATE psychology_demo_notifications
                    SET status='failed' WHERE order_id=%s AND recipient_role=%s""",
                    (order,role))


@demo_bp.route("/demo/return",methods=["POST","GET"])
def returning():
    if not pilot_enabled():
        return _error("Integración de prueba deshabilitada.",503)
    cancelled=request.values.get("TBK_TOKEN")
    token=request.values.get("token_ws") or cancelled
    if not isinstance(token,str) or not 20 <= len(token) <= 100 or not token.isalnum():
        return _error("Transbank no proporcionó un identificador válido.",400)
    try:
        with _database_connection() as conn:
            payment=conn.execute("SELECT * FROM psychology_demo_payments WHERE token_sha256=%s FOR UPDATE",
                                 (_token_digest(token),)).fetchone()
            if not payment:
                return _error("Operación de prueba no reconocida.",404)
            order=payment["order_id"]
            if cancelled:
                if payment["state"] in ("PAYMENT_PENDING","COMMITTING"):
                    _mark_cancelled(conn,order)
                return redirect(url_for("psychology_checkout_trial.receipt",order=order),code=303)
            if payment["state"] in ("PAYMENT_PENDING","COMMITTING"):
                old_state=payment["state"]
                conn.execute("UPDATE psychology_demo_payments SET state='COMMITTING' WHERE order_id=%s",(order,))
                try:
                    response=(gateway().commit(token) if old_state=="PAYMENT_PENDING"
                              else gateway().status(token))
                except GatewayError:
                    try:
                        response=gateway().status(token)
                    except GatewayError:
                        conn.execute("""UPDATE psychology_demo_payments
                                        SET state='RECONCILIATION_REQUIRED' WHERE order_id=%s""",(order,))
                        return _error("No fue posible verificar el resultado con Transbank. Revisar conciliación.",503)
                if not isinstance(response,dict) or (
                    response.get("buy_order")!=order or response.get("session_id")!=payment["session_id"]
                    or response.get("amount")!=payment["amount_clp"]):
                    conn.execute("""UPDATE psychology_demo_payments
                                    SET state='RECONCILIATION_REQUIRED' WHERE order_id=%s""",(order,))
                    return _error("Inconsistencia bancaria: se requiere conciliación.",503)
                if is_authorized(response,expected_order=order,expected_session=payment["session_id"],
                                 expected_amount=payment["amount_clp"]):
                    conn.execute("""UPDATE psychology_demo_payments SET state='AUTHORIZED',
                                   verified_at=now(),updated_at=now() WHERE order_id=%s""",(order,))
                else:
                    _mark_rejected(conn,order)
        _confirm_calendar(order)
        return redirect(url_for("psychology_checkout_trial.receipt",order=order),code=303)
    except Exception:
        return _error("No se pudo cerrar la operación. Revisa el estado antes de intentar otra compra.",503)


@demo_bp.get("/demo/receipt/<order>")
def receipt(order):
    if not pilot_enabled():
        return _error("Ensayo no habilitado",503)
    if not isinstance(order,str) or len(order)!=19 or not order.startswith("PSI"):
        return _error("Orden inválida.",400)
    try:
        with _database_connection() as conn:
            row=conn.execute("""SELECT p.state,p.order_id,p.service,p.amount_clp,p.calendar_event_id,
                                      h.starts_at,h.ends_at FROM psychology_demo_payments p
                     JOIN psychology_booking_holds h ON h.id=p.hold_id WHERE p.order_id=%s""",(order,)).fetchone()
            mails=conn.execute("SELECT recipient_role,status FROM psychology_demo_notifications WHERE order_id=%s",(order,)).fetchall()
    except Exception:
        return _error("No pudimos consultar el estado de la operación.",503)
    if not row:
        return _error("Operación desconocida.",404)
    states={"CONFIRMED":"Pago aprobado y evento de prueba creado en Google Calendar",
            "CALENDAR_PENDING":"Transbank aprobado; escritura de Calendar todavía desactivada",
            "RECONCILIATION_REQUIRED":"Resultado pendiente de conciliación; ninguna cita confirmada",
            "REJECTED":"Pago de prueba rechazado, hora liberada",
            "CANCELLED":"Compra de prueba cancelada, hora liberada",
            "AUTHORIZED":"Transbank aprobado, procesando agenda",
            "PAYMENT_PENDING":"Transacción pendiente de confirmar"}
    label=states.get(row["state"],"Operación de prueba no finalizada")
    mail_desc="Envío no configurado."
    if mails:
        mail_desc=", ".join(("Profesional" if x["recipient_role"]=="professional" else "Paciente de prueba")+": "+
                            ("enviado" if x["status"]=="sent" else "pendiente o falló") for x in mails)
    details=("<p><b>Orden:</b> "+escape(row["order_id"])+"</p>"
             "<p><b>Servicio:</b> "+escape(row["service"])+"</p>"
             "<p><b>Fecha y hora:</b> "+escape(row["starts_at"].astimezone(TZ).strftime("%d/%m/%Y %H:%M"))+"</p>"
             "<p><b>Correos:</b> "+escape(mail_desc)+"</p>"
             "<p><small>Solo prueba técnica. No se ha cobrado dinero real ni emitido boleta.</small></p>"
             "<a href='/demo'>Realizar otra prueba</a>")
    return _page(label,details)
