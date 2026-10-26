"""Registro privado de comprobantes Fonasa MLE.

No verifica el bono con Fonasa. El registro debe invocarse únicamente desde
un flujo servidor autenticado que haya creado la reserva real.
No exponer este módulo mediante un endpoint público sin controles adicionales.
"""
import re
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS fonasa_bonos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folio TEXT NOT NULL UNIQUE,
    reservation_id TEXT NOT NULL UNIQUE,
    file_key TEXT NOT NULL UNIQUE,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registrado'
        CHECK(status IN ('registrado','atendido','anulado')),
    registered_at TEXT NOT NULL,
    CHECK(length(file_sha256) = 64)
);
CREATE INDEX IF NOT EXISTS fonasa_bonos_status_idx
ON fonasa_bonos(status);
"""


class BonoDuplicado(ValueError):
    pass


def normalizar_folio(folio):
    """Solo folios numéricos: no convertir a int ni perder ceros iniciales."""
    if not isinstance(folio, str):
        raise ValueError("Folio inválido")
    value = re.sub(r"[.\s-]", "", folio)
    if not re.fullmatch(r"[0-9]{5,30}", value):
        raise ValueError("Folio inválido")
    return value


def registrar_bono(conn: sqlite3.Connection, *, folio: str,
                   reservation_id: str, file_key: str, file_sha256: str):
    """Inserción transaccional, con unicidad de folio, reserva y archivo.

    El archivo debe estar previamente validado y guardado en almacenamiento
    privado duradero. No guardar PDFs ni datos personales en GitHub.
    """
    normalized = normalizar_folio(folio)
    if not isinstance(reservation_id, str) or not 1 <= len(reservation_id) <= 128:
        raise ValueError("Reserva inválida")
    if not isinstance(file_key, str) or not file_key.startswith("fonasa/") or len(file_key) > 256:
        raise ValueError("Referencia privada de archivo inválida")
    if not isinstance(file_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", file_sha256):
        raise ValueError("Hash de archivo inválido")
    conn.executescript(SCHEMA)
    try:
        with conn:
            conn.execute(
                """INSERT INTO fonasa_bonos
                   (folio,reservation_id,file_key,file_sha256,registered_at)
                   VALUES (?,?,?,?,?)""",
                (normalized, reservation_id, file_key, file_sha256,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )
    except sqlite3.IntegrityError as exc:
        raise BonoDuplicado("Bono o reserva ya registrado") from exc
    return normalized


def buscar_por_reserva(conn: sqlite3.Connection, reservation_id: str):
    """Consulta exclusiva del panel privado; no divulgar a terceros."""
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    row = conn.execute(
        "SELECT folio,status,registered_at,file_key FROM fonasa_bonos WHERE reservation_id=?",
        (reservation_id,)
    ).fetchone()
    return dict(row) if row else None
