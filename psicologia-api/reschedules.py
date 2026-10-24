"""Auditable manual rescheduling of paid appointments.

This API records and validates administrative changes; it does NOT write to
Google Calendar. The operator must synchronize the calendar before confirming.
"""
import hmac
import os
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from flask import Blueprint, Response, jsonify, request
from werkzeug.security import check_password_hash
from billing import configured, db

reschedules_bp = Blueprint("reschedules", __name__)
TZ = ZoneInfo("America/Santiago")
SCHEMA = """
CREATE TABLE IF NOT EXISTS reservation_reschedules (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 order_id TEXT NOT NULL UNIQUE,
 old_date TEXT NOT NULL,
 old_time TEXT NOT NULL,
 new_date TEXT NOT NULL,
 new_time TEXT NOT NULL,
 requested_at TEXT NOT NULL,
 confirmed_at TEXT,
 actor TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 reason TEXT,
 medical_proof_verified INTEGER NOT NULL DEFAULT 0,
 calendar_reference TEXT
);
CREATE INDEX IF NOT EXISTS reschedule_status_idx
 ON reservation_reschedules(status);
"""

def private(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not configured():
            return jsonify(error="Almacenamiento persistente no configurado"), 503
        auth = request.authorization
        username = os.environ.get("PSICOLOGIA_BILLING_ADMIN_USER", "carlos")
        secret = os.environ.get("PSICOLOGIA_BILLING_ADMIN_HASH", "")
        supplied = auth.password if auth else ""
        valid = (check_password_hash(secret, supplied)
                 if secret.startswith(("scrypt:", "pbkdf2:"))
                 else hmac.compare_digest(supplied.encode(), os.environ.get("PSICOLOGIA_BILLING_ADMIN_PASSWORD", "").encode()))
        if not auth or auth.type != "basic" or not hmac.compare_digest(auth.username.encode(), username.encode()) or not valid:
            return Response("Acceso privado", status=401, headers={"WWW-Authenticate": 'Basic realm="Administración PSC"'})
        if request.method == "POST" and request.headers.get("X-Reschedule-Action") != "manual-reschedule":
            return jsonify(error="Confirmación administrativa requerida"), 403
        return view(*args, **kwargs)
    return wrapped

@reschedules_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def appointment(day, hour):
    if not isinstance(day, str) or not isinstance(hour, str):
        raise ValueError()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or not re.fullmatch(r"\d{2}:\d{2}", hour):
        raise ValueError()
    result = datetime.fromisoformat(day + "T" + hour).replace(tzinfo=TZ)
    if result.strftime("%Y-%m-%d") != day or result.strftime("%H:%M") != hour:
        raise ValueError()
    return result

@reschedules_bp.get("/admin/reprogramaciones")
@private
def list_reschedules():
    with db() as con:
        con.executescript(SCHEMA)
        rows = con.execute("SELECT * FROM reservation_reschedules ORDER BY requested_at DESC LIMIT 200").fetchall()
        return jsonify(items=[dict(x) for x in rows])

@reschedules_bp.post("/admin/reprogramaciones/<order_id>/solicitar")
@private
def request_change(order_id):
    data = request.get_json(silent=True) or {}
    try:
        new_start = appointment(data.get("new_date"), data.get("new_time"))
    except (ValueError, TypeError):
        return jsonify(error="Fecha u hora inválida"), 400
    now = datetime.now(TZ)
    if new_start <= now:
        return jsonify(error="La nueva hora debe estar en el futuro"), 400
    if len(str(data.get("reason", ""))) > 500:
        return jsonify(error="Motivo demasiado extenso"), 400
    medical = data.get("medical_proof_verified") is True
    with db() as con:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        order = con.execute("SELECT * FROM billing_orders WHERE order_id=?", (order_id,)).fetchone()
        if not order:
            return jsonify(error="Orden no encontrada"), 404
        if order["payment_status"] != "approved":
            return jsonify(error="La orden no registra pago aprobado"), 409
        old_start = appointment(order["appointment_date"], order["appointment_time"])
        if old_start <= now:
            return jsonify(error="La hora original ya pasó"), 409
        if new_start == old_start:
            return jsonify(error="Selecciona un horario diferente"), 400
        if con.execute("SELECT 1 FROM reservation_reschedules WHERE order_id=?", (order_id,)).fetchone():
            return jsonify(error="Ya existe una solicitud o reprogramación registrada para esta orden"), 409
        if old_start - now < timedelta(hours=24):
            if not (old_start.date() == now.date() and medical):
                return jsonify(error="Se exigen 24 horas de anticipación. El mismo día requiere justificativo médico verificado."), 409
        con.execute("""INSERT INTO reservation_reschedules
          (order_id,old_date,old_time,new_date,new_time,requested_at,actor,reason,medical_proof_verified)
          VALUES (?,?,?,?,?,?,?,?,?)""",
          (order_id,order["appointment_date"],order["appointment_time"],
           new_start.date().isoformat(),new_start.strftime("%H:%M"),
           now.isoformat(),request.authorization.username,
           str(data.get("reason","")),int(medical)))
    return jsonify(ok=True,status="pending",message="Solicitud registrada. Actualiza y verifica Google Calendar antes de confirmarla."), 201

@reschedules_bp.post("/admin/reprogramaciones/<order_id>/confirmar")
@private
def confirm_change(order_id):
    data = request.get_json(silent=True) or {}
    reference = data.get("calendar_reference")
    if data.get("calendar_updated") is not True or not isinstance(reference, str) or not 4 <= len(reference.strip()) <= 200:
        return jsonify(error="Confirma primero el cambio en Google Calendar y proporciona su referencia"), 400
    with db() as con:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        item = con.execute("SELECT * FROM reservation_reschedules WHERE order_id=?", (order_id,)).fetchone()
        if not item:
            return jsonify(error="Solicitud no encontrada"), 404
        if item["status"] != "pending":
            return jsonify(error="La solicitud ya se procesó"), 409
        # Update the ledger only after the operator confirms the calendar change.
        changed = con.execute("""UPDATE billing_orders SET appointment_date=?,appointment_time=?
         WHERE order_id=? AND appointment_date=? AND appointment_time=?""",
         (item["new_date"],item["new_time"],order_id,item["old_date"],item["old_time"]))
        if changed.rowcount != 1:
            return jsonify(error="La reserva original cambió; verifica antes de continuar"), 409
        con.execute("""UPDATE reservation_reschedules
         SET status='confirmed',confirmed_at=?,calendar_reference=? WHERE order_id=?""",
         (datetime.now(TZ).isoformat(),reference.strip(),order_id))
    return jsonify(ok=True,status="confirmed",message="Cambio registrado tras confirmación administrativa del calendario")
