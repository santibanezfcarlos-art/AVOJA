"""Migra SOLO la base PostgreSQL temporal para piloto, si se habilita expresamente.

Script de arranque de servicio independiente. No toca base Valhalla ni
registros de pacientes. Crea exclusivamente las tablas schema_holds.sql.
"""
import os
from pathlib import Path
import sys



def _run_smoke_test():
    """Ejecuta una operación de prueba en la base aislada y limpia sus filas."""
    from datetime import datetime, timedelta
    from booking_holds import _new_hold, _cancel_hold, _database_connection, HoldProblem, TZ

    if (os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") != "true"
            or os.environ.get("PSICOLOGIA_HOLDS_ENABLED", "false") != "false"
            or os.environ.get("PSICOLOGIA_CHECKOUT_MODE", "disabled") != "disabled"
            or os.environ.get("PSICOLOGIA_WEBPAY_MODE", "disabled") != "disabled"):
        raise RuntimeError("El diagnóstico solo funciona con reservas y pagos deshabilitados")
    start = (datetime.now(TZ) + timedelta(days=55)).replace(
        hour=18, minute=0, second=0, microsecond=0)
    day, end = start.date(), start + timedelta(minutes=45)
    created_ids = []
    try:
        first, first_secret = _new_hold("psicoterapia", day, start, end)
        created_ids.append(first)
        try:
            second, _ = _new_hold("psicoterapia", day, start, end)
        except HoldProblem as exc:
            if exc.status != 409:
                raise RuntimeError("No se bloqueó el solapamiento correctamente") from exc
        else:
            created_ids.append(second)
            raise RuntimeError("La misma hora se retuvo dos veces")
        if _cancel_hold(first, first_secret) != "CANCELLED":
            raise RuntimeError("No se pudo cancelar una retención")
        third, _ = _new_hold("psicoterapia", day, start, end)
        created_ids.append(third)
    finally:
        # Ni las retenciones ni sus identificadores aparecen en logs.
        if created_ids:
            import uuid
            with _database_connection() as conn:
                conn.execute("DELETE FROM psychology_booking_holds WHERE id = ANY(%s)",
                             ([uuid.UUID(i) for i in created_ids],))
    print("PSICOLOGIA_PILOT_SMOKE: hold_created, duplicate_blocked, cancelled, slot_released", flush=True)



def _probe_google_calendar_read():
    """Verifica credenciales y permiso de lectura sin revelar eventos, claves ni datos personales."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from google_calendar_writer import CalendarWriter, CalendarHttpError, CalendarIntegrationError

    if (os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") != "true"
            or os.environ.get("PSICOLOGIA_CALENDAR_WRITE_ENABLED", "false") != "false"):
        print("PSICOLOGIA_GOOGLE_READ_CHECK: unsafe_pilot_config", flush=True)
        return
    try:
        writer = CalendarWriter()
        if not writer.settings.configured:
            print("PSICOLOGIA_GOOGLE_READ_CHECK: credentials_or_id_missing", flush=True)
            return
        now = datetime.now(ZoneInfo("America/Santiago"))
        writer.list_busy(now, now + timedelta(minutes=30))
        print("PSICOLOGIA_GOOGLE_READ_CHECK: authorized", flush=True)
    except CalendarHttpError as ex:
        print("PSICOLOGIA_GOOGLE_READ_CHECK: google_http_status=" + str(ex.status), flush=True)
    except CalendarIntegrationError:
        print("PSICOLOGIA_GOOGLE_READ_CHECK: credentials_or_calendar_access_error", flush=True)
    except Exception as ex:
        print("PSICOLOGIA_GOOGLE_READ_CHECK: error_type=" + type(ex).__name__, flush=True)



def main():
    if os.environ.get("PSICOLOGIA_HOLDS_SCHEMA_BOOTSTRAP") != "true":
        print("PSICOLOGIA_PILOT_SCHEMA: bootstrap_disabled", flush=True)
        return 0
    if os.environ.get("PSICOLOGIA_HOLDS_ISOLATED_PILOT") != "true":
        print("PSICOLOGIA_PILOT_SCHEMA: requires_isolated_pilot", flush=True)
        return 1
    url = os.environ.get("PSICOLOGIA_HOLDS_DATABASE_URL", "")
    if not url:
        print("PSICOLOGIA_PILOT_SCHEMA: awaiting_database_configuration", flush=True)
        return 0
    try:
        import psycopg
        if not url.startswith(("postgresql://", "postgres://")):
            print("PSICOLOGIA_PILOT_SCHEMA: invalid_connection_protocol", flush=True)
            return 1
        sql = Path(__file__).with_name("schema_holds.sql").read_text("utf-8")
        with psycopg.connect(url, connect_timeout=10, application_name="psychology-pilot-bootstrap") as conn:
            conn.execute(sql)
        print("PSICOLOGIA_PILOT_SCHEMA: ready", flush=True)
        from demo_notifications import configured as mail_is_ready
        print("PSICOLOGIA_PILOT_EMAIL: " + ("ready" if mail_is_ready() else "not_configured"), flush=True)
        if os.environ.get("PSICOLOGIA_GOOGLE_READ_PROBE") == "true":
            _probe_google_calendar_read()
        if os.environ.get("PSICOLOGIA_HOLDS_DB_SELFTEST") == "true":
            _run_smoke_test()
        return 0
    except Exception as ex:
        print("PSICOLOGIA_PILOT_SCHEMA: error_type=" + type(ex).__name__, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
