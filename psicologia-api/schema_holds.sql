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

-- Solo ensayos: identificadores Transbank y estado de confirmación.
-- No se guardan números de tarjetas, RUT, fichas clínicas ni pacientes reales.
BEGIN;
CREATE TABLE IF NOT EXISTS psychology_demo_payments (
  order_id varchar(26) PRIMARY KEY,
  hold_id uuid NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
  session_id varchar(61) NOT NULL UNIQUE,
  token_sha256 char(64) UNIQUE,
  service varchar(32) NOT NULL CHECK(service IN ('psicoterapia','empleabilidad','competencias')),
  amount_clp integer NOT NULL CHECK(amount_clp > 0),
  test_recipient varchar(254) NOT NULL,
  state varchar(32) NOT NULL DEFAULT 'STARTING'
    CHECK(state IN ('STARTING','PAYMENT_PENDING','COMMITTING','AUTHORIZED',
                    'REJECTED','CANCELLED','CALENDAR_PENDING','CONFIRMED',
                    'RECONCILIATION_REQUIRED')),
  calendar_event_id varchar(80),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  verified_at timestamptz
);
CREATE INDEX IF NOT EXISTS psychology_demo_payments_states
  ON psychology_demo_payments(state,created_at);
CREATE TABLE IF NOT EXISTS psychology_demo_notifications (
  order_id varchar(26) NOT NULL REFERENCES psychology_demo_payments(order_id),
  recipient_role varchar(16) NOT NULL CHECK(recipient_role IN ('professional','patient')),
  status varchar(16) NOT NULL DEFAULT 'pending'
    CHECK(status IN ('pending','sending','sent','failed')),
  attempted_at timestamptz,
  sent_at timestamptz,
  PRIMARY KEY(order_id,recipient_role)
);
COMMIT;

-- Hora de Chile: marca la corrección del correo antiguo una sola vez.
-- Idempotencia también protegida por clave específica en Resend.
ALTER TABLE psychology_demo_notifications
    ADD COLUMN IF NOT EXISTS timezone_correction_sent_at timestamptz;

-- Enlaces privados y auditoría: piloto de reagendamiento (desactivado por defecto).
-- El token sin cifrar solo se entrega al paciente mediante el correo.
BEGIN;
CREATE TABLE IF NOT EXISTS psychology_reschedule_links (
 order_id varchar(26) PRIMARY KEY REFERENCES psychology_demo_payments(order_id),
 token_sha256 char(64) NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 redeemed_at timestamptz,
 revoked_at timestamptz
);
CREATE TABLE IF NOT EXISTS psychology_reschedule_audit (
 id bigserial PRIMARY KEY,
 order_id varchar(26) NOT NULL REFERENCES psychology_demo_payments(order_id),
 old_starts_at timestamptz NOT NULL,
 new_starts_at timestamptz NOT NULL,
 changed_at timestamptz NOT NULL DEFAULT now(),
 channel varchar(20) NOT NULL DEFAULT 'patient_link',
 CONSTRAINT one_completed_reschedule UNIQUE(order_id)
);
COMMIT;

-- Nombre visible solamente en avisos profesionales y evento privado.
ALTER TABLE psychology_demo_payments ADD COLUMN IF NOT EXISTS patient_name varchar(120);

CREATE TABLE IF NOT EXISTS psychology_google_oauth (key text PRIMARY KEY,encrypted_refresh_token text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now());
