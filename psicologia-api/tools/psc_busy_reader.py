"""PSC PostgreSQL replacement candidate: READ-ONLY busy intervals from SQLite.

This is deliberately NOT connected to the public agenda. It reads only a
verified SQLite staging copy, and returns interval boundaries, never patient
names or identifiers. All timestamps must carry timezone information.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sqlite3
from urllib.parse import quote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
CAPACITY = 3
BLOCKING_STATES = {"HELD", "PAYMENT_PENDING", "RECONCILIATION_REQUIRED"}


def busy_intervals(db_path, first, last, *, now=None):
    """Returns exact parity with existing PG demo/busy slot and capacity rules."""
    if not isinstance(first, date) or isinstance(first, datetime):
        raise ValueError("Fecha inicial inválida")
    if not isinstance(last, date) or isinstance(last, datetime):
        raise ValueError("Fecha final inválida")
    if last < first or (last - first).days > 21:
        raise ValueError("Rango inválido")
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Se requiere hora con zona")
    path = Path(db_path)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError("SQLite de ensayo no disponible")
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(
            """SELECT slot_date,starts_at,ends_at,status,expires_at
               FROM psychology_booking_holds
               WHERE slot_date BETWEEN ? AND ?""",
            (first.isoformat(), last.isoformat()),
        ).fetchall()

    windows = []
    count_by_day = Counter()
    for row in rows:
        status = row["status"]
        expires = datetime.fromisoformat(row["expires_at"])
        start = datetime.fromisoformat(row["starts_at"])
        end = datetime.fromisoformat(row["ends_at"])
        if (expires.tzinfo is None or start.tzinfo is None or
                end.tzinfo is None or end <= start):
            raise ValueError("Intervalo almacenado inválido")
        if status != "CONFIRMED" and not (status in BLOCKING_STATES and expires > now):
            continue
        day = start.astimezone(TZ).date()
        if day.isoformat() != row["slot_date"]:
            raise ValueError("Fecha de reserva inconsistente")
        count_by_day[day] += 1
        windows.append((start, end))
    for day, total in count_by_day.items():
        if total >= CAPACITY:
            first_edge = datetime.combine(day, time.min, tzinfo=TZ)
            last_edge = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ)
            windows.append((first_edge, last_edge))
    return windows


def serialize(windows):
    return [[start.isoformat(), end.isoformat()] for start, end in windows]
