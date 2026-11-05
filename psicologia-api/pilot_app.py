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
