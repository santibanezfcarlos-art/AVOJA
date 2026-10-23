-- Esquema de pagos y reservas de Psicología Carlos Santibáñez.
-- Ejecutar UNA SOLA VEZ en una base PostgreSQL PROPIA (no comparte Valhalla).
-- No almacenar información clínica ni números de tarjetas.
CREATE TABLE IF NOT EXISTS psychology_booking_days (
    slot_date DATE PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS psychology_booking_holds (
    id UUID PRIMARY KEY,
    slot_date DATE NOT NULL REFERENCES psychology_booking_days(slot_date),
    starts_at TIME NOT NULL,
    ends_at TIME NOT NULL,
    patient_name VARCHAR(120) NOT NULL,
    patient_email VARCHAR(254) NOT NULL,
    status VARCHAR(30) NOT NULL CHECK (status IN
        ('HELD','PAYMENT_PENDING','PROCESSING_RETURN','CONFIRMED','EXPIRED','DECLINED','CANCELLED','RECONCILIATION_REQUIRED','PAID_UNALLOCATED')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    CHECK (ends_at > starts_at)
);
CREATE INDEX IF NOT EXISTS psychology_holds_by_day
ON psychology_booking_holds(slot_date,starts_at,status,expires_at);
CREATE TABLE IF NOT EXISTS psychology_payment_attempts (
    id UUID PRIMARY KEY,
    hold_id UUID NOT NULL UNIQUE REFERENCES psychology_booking_holds(id),
    buy_order VARCHAR(26) NOT NULL UNIQUE,
    session_id VARCHAR(44) NOT NULL UNIQUE,
    amount_clp INTEGER NOT NULL CHECK (amount_clp=30000),
    environment VARCHAR(20) NOT NULL CHECK (environment IN ('integration','production')),
    token_ws VARCHAR(110) UNIQUE,
    checkout_url TEXT,
    state VARCHAR(32) NOT NULL CHECK (state IN
        ('CREATING','READY','COMMITTING','APPROVED','REJECTED','CANCELLED','RECONCILIATION_REQUIRED','PAID_UNALLOCATED')),
    provider_response_code INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    committed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS psychology_attempts_state_idx
ON psychology_payment_attempts(state,updated_at);
-- La lógica del servidor bloquea psychology_booking_days FOR UPDATE antes
-- de contar cupos e insertar/confirmar reservas.
-- Los bloqueos expiran por fecha; CONFIRMED nunca expira.
