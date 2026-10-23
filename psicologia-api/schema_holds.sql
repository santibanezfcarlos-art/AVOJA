-- Piloto de retención de horas. Solo el backend de Psicología usa esta BD.
-- Ejecutar en PostgreSQL exclusivo; no utilizar la BD de Valhalla Office.
BEGIN;
CREATE TABLE IF NOT EXISTS psychology_booking_days (
    slot_date date PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS psychology_booking_holds (
    id uuid PRIMARY KEY,
    cancel_secret_sha256 char(64) NOT NULL,
    slot_date date NOT NULL REFERENCES psychology_booking_days(slot_date),
    starts_at timestamptz NOT NULL,
    ends_at timestamptz NOT NULL,
    service varchar(32) NOT NULL CHECK (service IN ('psicoterapia','empleabilidad','competencias')),
    status varchar(30) NOT NULL DEFAULT 'HELD'
        CHECK (status IN ('HELD','EXPIRED','CANCELLED','PAYMENT_PENDING',
                         'RECONCILIATION_REQUIRED','CONFIRMED')),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)
);
CREATE INDEX IF NOT EXISTS psychology_holds_date_state
    ON psychology_booking_holds(slot_date,status,expires_at);
-- La fila del día se bloquea FOR UPDATE antes de contar cupos y crear/cancelar holds.
-- El diseño es intencionalmente sin nombres, RUT, teléfonos, tarjetas ni información clínica.
COMMIT;
