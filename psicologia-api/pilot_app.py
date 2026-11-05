"""Servicio de ensayo SEPARADO de la agenda publicada.

Solo expone pruebas de retención; no registra las rutas de Transbank,
Google Calendar ni páginas de pacientes. Las operaciones requieren clave
privada y permanecen deshabilitadas por defecto.
"""
import os
from flask import Flask, jsonify

from booking_holds import bp, enabled

app = Flask(__name__)
app.register_blueprint(bp)

# Checkout de pruebas aislado; no habilita cobros ni pacientes reales.
from demo_checkout import demo_bp, pilot_enabled
app.register_blueprint(demo_bp)

from public_demo_bridge import public_bridge_bp
app.register_blueprint(public_bridge_bp)
from production_fulfillment import bp as production_fulfillment_bp
app.register_blueprint(production_fulfillment_bp)
from fonasa_calendar_fulfillment import bp as fonasa_calendar_fulfillment_bp
app.register_blueprint(fonasa_calendar_fulfillment_bp)

from rebooking_preview import rebooking_bp
app.register_blueprint(rebooking_bp)

from google_oauth_bootstrap import oauth_bp
app.register_blueprint(oauth_bp)


@app.get("/")
@app.get("/health")
def health():
    """Diagnóstico sin divulgar conexiones ni claves privadas."""
    db_configured = bool(os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL"))
    schema_ready = False
    database_reachable = False
    if db_configured:
        try:
            from booking_holds import _database_connection
            with _database_connection() as conn:
                row = conn.execute(
                    "SELECT to_regclass('public.psychology_booking_holds') IS NOT NULL AS ready"
                ).fetchone()
                database_reachable = True
                schema_ready = bool(row["ready"])
        except Exception:
            pass
    return jsonify(
        service="psicologia-retenciones-piloto",
        mode="TEST_ONLY",
        production_payments=False,
        patient_bookings=os.environ.get('PSICOLOGIA_PRODUCTION_FULFILLMENT_ENABLED')=='true',
        production_fulfillment_enabled=os.environ.get('PSICOLOGIA_PRODUCTION_FULFILLMENT_ENABLED')=='true',
        demo_checkout_enabled=pilot_enabled(),
        demo_notifications_configured=__import__("demo_notifications").configured(),
        public_demo_bridge_enabled=__import__("public_demo_bridge")._enabled(),
        database_configured=db_configured,
        database_reachable=database_reachable,
        schema_ready=schema_ready,
        holds_enabled=enabled(),
        google_api_configured=bool(os.environ.get("PSICOLOGIA_GOOGLE_CALENDAR_ID") and os.environ.get("PSICOLOGIA_GOOGLE_SERVICE_ACCOUNT_JSON")),
        google_api_write_enabled=os.environ.get("PSICOLOGIA_CALENDAR_WRITE_ENABLED") == "true",
        calendar_availability_source=os.environ.get("PSICOLOGIA_HOLDS_CALENDAR_POLICY", "ical"),
    )

# Explicitly opt-in to a one-shot, read-only encrypted snapshot.
# The default path is inert and does not touch production reservations.
if os.environ.get("PSC_PG_SNAPSHOT_PUSH_ON_START") == "true":
    import threading
    def _snapshot_on_start():
        import logging
        import time
        time.sleep(8)
        try:
            from tools.psc_push_snapshot import push_once
            result = push_once()
            logging.getLogger("psc_backup").warning(
                "PSC_PG_BACKUP state=%s tables=%s rows=%s",
                result["state"], result["table_count"], result["row_count"])
        except Exception as error:
            logging.getLogger("psc_backup").error(
                "PSC_PG_BACKUP state=FAILED error_type=%s", type(error).__name__)
    threading.Thread(target=_snapshot_on_start, daemon=True).start()

# Incremental continuity: encrypted snapshots to PSC's ALREADY PAID persistent disk.
# This is independent of live booking logic and may be disabled by environment.
# Never logs secrets or patient records.
if os.environ.get("PSC_PG_PERIODIC_BACKUP_ENABLED") == "true":
    import threading as _periodic_threading
    def _psc_pg_periodic_backup_loop():
        import logging as _logging
        import time as _time
        from tools.psc_push_snapshot import push_once
        logger = _logging.getLogger("psc_periodic_backup")
        # Let the production checkout API finish startup first.
        _time.sleep(15)
        while True:
            try:
                outcome = push_once()
                logger.warning(
                    "PSC_PG_PERIODIC_BACKUP state=%s tables=%s rows=%s",
                    outcome["state"], outcome["table_count"], outcome["row_count"])
            except Exception as exc:
                logger.error(
                    "PSC_PG_PERIODIC_BACKUP state=FAILED error_type=%s",
                    type(exc).__name__)
            # Retains multiple historical checkpoints. No destructive pruning.
            _time.sleep(6 * 3600)
    _periodic_threading.Thread(
        target=_psc_pg_periodic_backup_loop, daemon=True).start()

# Read-only migration diagnostic; does not modify Calendar or OAuth state.
if os.environ.get("PSC_GOOGLE_OAUTH_DISK_PROBE") == "true":
    import threading as _oauth_probe_threading
    def _probe_oauth_disk_source():
        import logging as _logging
        import time as _time
        _time.sleep(12)
        try:
            from cryptography.fernet import Fernet
            from psc_calendar_disk_client import fetch_encrypted_refresh_token
            encrypted = fetch_encrypted_refresh_token()
            secret = Fernet(
                os.environ["PSICOLOGIA_GOOGLE_OAUTH_FERNET_KEY"].encode()
            ).decrypt(encrypted.encode())
            if not secret or len(secret) < 16:
                raise RuntimeError("OAuth refresh token invalid")
            _logging.getLogger("psc_oauth_migration").warning(
                "PSC_GOOGLE_OAUTH_DISK_PROBE state=DECRYPTED_OK")
        except Exception as exc:
            _logging.getLogger("psc_oauth_migration").error(
                "PSC_GOOGLE_OAUTH_DISK_PROBE state=FAILED error_type=%s",
                type(exc).__name__)
    _oauth_probe_threading.Thread(target=_probe_oauth_disk_source,daemon=True).start()

# One-time non-destructive seed of the dedicated PSC OAuth SQLite store.
if os.environ.get("PSC_GOOGLE_OAUTH_DISK_SEED_ON_START") == "true":
    import threading as _psc_oauth_seed_threading
    def _seed_psc_oauth_disk():
        import logging as _logging
        import time as _time
        _time.sleep(12)
        try:
            from psc_calendar_disk_client import (
                fetch_encrypted_refresh_token, save_encrypted_refresh_token)
            original = fetch_encrypted_refresh_token()
            save_encrypted_refresh_token(original)
            if fetch_encrypted_refresh_token() != original:
                raise RuntimeError("OAuth ciphertext roundtrip mismatch")
            _logging.getLogger("psc_oauth_migration").warning(
                "PSC_GOOGLE_OAUTH_STORE state=SEEDED_AND_ROUNDTRIP_VERIFIED")
        except Exception as exc:
            _logging.getLogger("psc_oauth_migration").error(
                "PSC_GOOGLE_OAUTH_STORE state=FAILED error_type=%s",
                type(exc).__name__)
    _psc_oauth_seed_threading.Thread(
        target=_seed_psc_oauth_disk, daemon=True).start()

# End-to-end read-only check: PSC API -> pilot signed busy -> Google Calendar.
# This catches the former circular OAuth HTTP dependency, without bookings.
if os.environ.get("PSC_PUBLIC_AVAILABILITY_PROBE_ON_START") == "true":
    import threading as _psc_availability_test_thread
    def _psc_public_availability_probe():
        import logging as _logging
        import time as _time
        import json as _json
        from datetime import datetime as _datetime, timedelta as _timedelta
        from urllib.request import urlopen as _urlopen
        from urllib.parse import urlencode as _urlencode
        from zoneinfo import ZoneInfo as _ZoneInfo
        logger = _logging.getLogger("psc_availability_smoke")
        _time.sleep(15)
        day = (_datetime.now(_ZoneInfo("America/Santiago"))
               + _timedelta(days=1)).date().isoformat()
        url = ("https://psicologia-carlos-calendar-api.onrender.com/api/availability?"
               + _urlencode({"from": day, "to": day, "duration": 45}))
        for attempt in range(1, 4):
            try:
                with _urlopen(url, timeout=25) as response:
                    body = _json.loads(response.read(20000))
                if body.get("status") != "ok" or day not in body.get("blocked", {}):
                    raise RuntimeError("Availability returned invalid payload")
                logger.warning("PSC_AVAILABILITY_E2E state=OK_WITH_DISK_OAUTH")
                break
            except Exception as exc:
                if attempt == 3:
                    logger.error("PSC_AVAILABILITY_E2E state=FAILED error_type=%s",
                                 type(exc).__name__)
                else:
                    _time.sleep(8)
    _psc_availability_test_thread.Thread(
        target=_psc_public_availability_probe,daemon=True).start()

# OAuth credential acquisition must NEVER block Gunicorn startup. The signed
# PSC endpoint is a remote dependency and may time out when Render wakes up.
# Periodically refresh ciphertext only; no calendar writes or token logging.
if os.environ.get("PSC_GOOGLE_OAUTH_STORAGE") == "psc_disk":
    import threading as _psc_oauth_thread
    def _psc_refresh_oauth_cache():
        import logging as _logging
        import time as _time
        logger = _logging.getLogger("psc_oauth_cache")
        _time.sleep(2)
        retry_seconds = 15
        while True:
            try:
                from psc_calendar_disk_client import (
                    refresh_cached_ciphertext, fetch_encrypted_refresh_token)
                from cryptography.fernet import Fernet
                refresh_cached_ciphertext()
                plain = Fernet(
                    os.environ["PSICOLOGIA_GOOGLE_OAUTH_FERNET_KEY"].encode()
                ).decrypt(fetch_encrypted_refresh_token().encode())
                if len(plain) < 16:
                    raise ValueError("OAuth token length invalid")
                logger.warning("PSC_GOOGLE_OAUTH_ASYNC_CACHE state=READY")
                retry_seconds = 900
            except Exception as exc:
                logger.warning(
                    "PSC_GOOGLE_OAUTH_ASYNC_CACHE state=RETRYING error_type=%s",
                    type(exc).__name__)
                retry_seconds = min(retry_seconds * 2, 300)
            _time.sleep(retry_seconds)
    _psc_oauth_thread.Thread(
        target=_psc_refresh_oauth_cache, daemon=True).start()
