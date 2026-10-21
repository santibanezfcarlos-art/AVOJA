"""One-shot, explicitly confirmed TTLock real-world integration test.

Never runs on import or as a background job. A manual test issues a *new* working
passcode and sends email. Preserves separate audit records for every attempt.
"""
import os
import sys
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ttlock_daily import (TTLockClient, AttemptLedger, _encryption_key,
                          encrypt_code, email_content, validity_window)
from mailer import EmailDelivery
from ttlock_email_html import access_email_html


def record(db_path, test_id, status, *, encrypted_code=None, passcode_id=None):
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("""UPDATE ttlock_manual_tests SET status=?, encrypted_code=COALESCE(?, encrypted_code),
                     passcode_id=COALESCE(?, passcode_id), updated_at=?
                     WHERE test_id=?""", (status, encrypted_code, passcode_id,
                                          datetime.now(timezone.utc).isoformat(), test_id))


def main():
    db_path = os.environ["VALHALLA_DB_PATH"]
    _encryption_key()  # Fail BEFORE calling TTLock.
    client = TTLockClient.from_environment()
    mailer = EmailDelivery()
    if not mailer.configured:
        raise RuntimeError("Email provider not configured")
    email_to = os.environ["TTLOCK_EMAIL_TO"]
    start, end = validity_window()
    if "--start-23" in sys.argv:
        today = datetime.now(start.tzinfo).date()
        start = datetime(today.year,today.month,today.day,23,tzinfo=start.tzinfo)
        tomorrow = today + timedelta(days=1)
        end = datetime(tomorrow.year,tomorrow.month,tomorrow.day,23,tzinfo=start.tzinfo)
    if datetime.now(timezone.utc) >= end.astimezone(timezone.utc):
        raise RuntimeError("Invalid validity window")

    print("PRUEBA REAL: creara UN codigo nuevo valido en TTLock y enviara un email.")
    print("Vigencia:", start.isoformat(), "hasta", end.isoformat())
    if input("Escribe GENERAR para continuar: ").strip() != "GENERAR":
        print("CANCELADO: no se creo ningun codigo")
        return

    test_id = str(uuid.uuid4())
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ttlock_manual_tests (
            test_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            passcode_id INTEGER, encrypted_code TEXT)""")
        stamp = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO ttlock_manual_tests(test_id,status,created_at,updated_at) VALUES (?,?,?,?)",
                   (test_id, "reserved", stamp, stamp))

    # Do not print the code or any secret. Authentication is read-only.
    try:
        token = client.authenticate()
        details = client.details(token)
    except Exception:
        record(db_path, test_id, "verification_failed")
        print("FALLO VERIFICACION. Ningun codigo solicitado.")
        return

    record(db_path, test_id, "generating")
    try:
        code, code_id = client.generate(token, start, end, details["keyboardPwdVersion"])
    except Exception:
        record(db_path, test_id, "uncertain")
        print("RESULTADO INCIERTO: revisa TTLock. NO repitas la prueba.")
        return

    try:
        record(db_path, test_id, "created", encrypted_code=encrypt_code(code), passcode_id=code_id)
    except Exception:
        print("CODIGO CREADO, PERO NO GUARDADO. Revisa TTLock; NO repitas.")
        return

    try:
        subject, body = email_content(code, start)
        mailer.send_message(email_to, subject, body,
                            idempotency_key="ttlock-manual-" + test_id,
                            html=access_email_html(code, start))
    except Exception:
        record(db_path, test_id, "email_failed")
        print("CODIGO GUARDADO; EMAIL FALLIDO. No generes otro.")
        return

    record(db_path, test_id, "emailed")
    print("RESULTADO: emailed. Revisa tu bandeja de entrada.")
    _distribute_manual(db_path, test_id, start, encrypt_code(code))


def _distribute_manual(db_path, test_id, start, encrypted_code):
    from ttlock_distribution import distribute_saved
    status = distribute_saved(db_path, "manual-" + test_id, start, encrypted_code)
    print("DISTRIBUCION:", status)


def distribute_latest():
    """Explicit resend-free distribution using the latest successfully emailed manual code."""
    db_path = os.environ["VALHALLA_DB_PATH"]
    with sqlite3.connect(db_path,timeout=10) as db:
        db.row_factory=sqlite3.Row
        try:
            row=db.execute("""SELECT test_id, created_at, encrypted_code
                FROM ttlock_manual_tests
                WHERE status='emailed' AND encrypted_code IS NOT NULL
                ORDER BY created_at DESC LIMIT 1""").fetchone()
        except sqlite3.OperationalError:
            row=None
    if not row:
        print("NO HAY CODIGO MANUAL GUARDADO PARA DISTRIBUIR")
        return
    stamp=datetime.fromisoformat(row["created_at"])
    start, end=validity_window(stamp)
    if datetime.now(timezone.utc)>=end.astimezone(timezone.utc):
        print("CODIGO MANUAL VENCIDO; NO SE ENVIO")
        return
    _distribute_manual(db_path,row["test_id"],start,row["encrypted_code"])


if __name__ == "__main__":
    if len(sys.argv)==2 and sys.argv[1]=="--distribute-latest":
        distribute_latest()
    elif len(sys.argv)==1 or (len(sys.argv)==2 and sys.argv[1]=="--start-23"):
        main()
    else:
        raise SystemExit("Uso: python ttlock_manual_test.py [--start-23 | --distribute-latest]")
