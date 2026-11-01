"""Isolated, disabled-by-default PSC SQLite busy-slot reader candidate.

This module does NOT change existing public bookings or Google Calendar.
It reads only a previously migrated SQLite file on the paid Render disk.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import os
import sqlite3
from urllib.parse import quote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Santiago")
CAPACITY = 3
BLOCKING = {"HELD", "PAYMENT_PENDING", "RECONCILIATION_REQUIRED"}


def configured_file():
    raw = os.environ.get("PSC_MIGRATION_HOLDS_SQLITE_PATH", "")
    if not raw or not os.path.isabs(raw):
        raise RuntimeError("SQLite migration store unavailable")
    path = Path(raw)
    # Prevent the auxiliary route from accidentally accessing the billing DB.
    billing = os.environ.get("PSICOLOGIA_BILLING_DB", "")
    if billing and os.path.abspath(raw) == os.path.abspath(billing):
        raise RuntimeError("Migration store cannot be billing ledger")
    if (not path.is_file() or path.is_symlink()
            or os.path.commonpath(("/var/data", str(path.resolve()))) != "/var/data"):
        raise RuntimeError("Migration store invalid or not on persistent disk")
    if os.environ.get("RENDER") and not os.path.ismount("/var/data"):
        raise RuntimeError("Render persistent disk is not mounted")
    return path


def read_intervals(first, last, *, now=None, db_path=None):
    if (not isinstance(first, date) or isinstance(first, datetime)
            or not isinstance(last, date) or isinstance(last, datetime)
            or last < first or (last - first).days > 21):
        raise ValueError("Invalid range")
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    path = Path(db_path) if db_path is not None else configured_file()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError("Migration file unavailable")
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=6) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        records = connection.execute(
            """SELECT slot_date,starts_at,ends_at,status,expires_at
               FROM psychology_booking_holds
               WHERE slot_date BETWEEN ? AND ?""",
            (first.isoformat(), last.isoformat()),
        ).fetchall()
    intervals = []
    counts = Counter()
    for item in records:
        start, end = (datetime.fromisoformat(item[name])
                      for name in ("starts_at", "ends_at"))
        expires = datetime.fromisoformat(item["expires_at"])
        if (start.tzinfo is None or end.tzinfo is None or
                expires.tzinfo is None or end <= start):
            raise RuntimeError("Invalid persisted hold timestamp")
        if item["status"] != "CONFIRMED" and not (
                item["status"] in BLOCKING and expires > now):
            continue
        chile_day = start.astimezone(TZ).date()
        if chile_day.isoformat() != item["slot_date"]:
            raise RuntimeError("Hold local date mismatch")
        intervals.append((start, end))
        counts[chile_day] += 1
    for day, number in counts.items():
        if number >= CAPACITY:
            intervals.append((
                datetime.combine(day, time.min, tzinfo=TZ),
                datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ),
            ))
    return [[a.isoformat(), b.isoformat()] for a, b in intervals]
