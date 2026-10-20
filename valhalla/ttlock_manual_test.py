"""One-shot, explicitly confirmed TTLock real-world integration test.

Never runs on import or as a background job. A manual test issues a *new* working
passcode and sends email. Preserves separate audit records for every attempt.
"""
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ttlock_daily import (TTLockClient, AttemptLedger, _encryption_key,
                          encrypt_code, email_content, validity_window)
from mailer import EmailDelivery


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
                            idempotency_key="ttlock-manual-" + test_id)
    except Exception:
        record(db_path, test_id, "email_failed")
        print("CODIGO GUARDADO; EMAIL FALLIDO. No generes otro.")
        return

    record(db_path, test_id, "emailed")
    print("RESULTADO: emailed. Revisa tu bandeja de entrada.")


if __name__ == "__main__":
    main()
