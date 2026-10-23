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
        patient_bookings=False,
        database_configured=db_configured,
        database_reachable=database_reachable,
        schema_ready=schema_ready,
        holds_enabled=enabled(),
    )
