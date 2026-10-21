"""Read-only next-day TTLock recipient selection for AVOJA.

No emails sent, no TTLock calls, no changes to bookings or SQLite schema.
The day is chosen from TTLock's 22:00 local issue date, not server UTC date.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHILE = ZoneInfo("America/Santiago")


def recipients_for_day(db_path, day):
    start = datetime(day.year, day.month, day.day, tzinfo=CHILE)
    next_day = day + timedelta(days=1)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=CHILE)
    start_utc = start.astimezone(timezone.utc).isoformat(timespec="seconds")
    end_utc = end.astimezone(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        rows = db.execute("""
            SELECT u.email FROM bookings AS b
            JOIN users AS u ON u.id=b.user_id
            WHERE b.status='confirmed'
              AND b.start >= ? AND b.start < ?
            ORDER BY b.start, b.id
        """, (start_utc, end_utc)).fetchall()
    # Deduplicate by actual address, not booking ID, retaining first occurrence.
    seen = set()
    recipients = []
    for (email,) in rows:
        address = (email or "").strip()
        if not address or "@" not in address:
            continue
        key = address.casefold()
        if key not in seen:
            seen.add(key)
            recipients.append(address)
    return recipients


def recipients_for_tomorrow(db_path, issued_at):
    if issued_at.tzinfo is None:
        raise ValueError("issued_at must be timezone-aware")
    day = issued_at.astimezone(CHILE).date() + timedelta(days=1)
    return day, recipients_for_day(db_path, day)
