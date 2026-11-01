"""PSC: independent parity check of PostgreSQL snapshot busy rules vs SQLite.

Checks only booking hold intervals and 3-a-day capacity, never Google events.
Uses one frozen snapshot timestamp; does not query live PostgreSQL. Data
identifiers, patients, token hashes and emails are not printed or transmitted.
"""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from psc_busy_reader import busy_intervals
from psc_import_snapshot_sqlite import load_snapshot

TZ = ZoneInfo("America/Santiago")
ACTIVE = {"HELD", "PAYMENT_PENDING", "RECONCILIATION_REQUIRED"}


def expected_windows(snapshot, day, *, now):
    active = []
    for row in snapshot["tables"]["psychology_booking_holds"]:
        if row["slot_date"] != day.isoformat():
            continue
        status = row["status"]
        if status != "CONFIRMED" and not (
            status in ACTIVE and datetime.fromisoformat(row["expires_at"]) > now
        ):
            continue
        start = datetime.fromisoformat(row["starts_at"])
        end = datetime.fromisoformat(row["ends_at"])
        if not start.tzinfo or not end.tzinfo:
            raise RuntimeError("Timestamp naive in export")
        active.append((start, end))
    if len(active) >= 3:
        active.append((datetime.combine(day, time.min, tzinfo=TZ),
                       datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ)))
    return active


def canonical(windows):
    return Counter((a.astimezone(timezone.utc), b.astimezone(timezone.utc))
                   for a, b in windows)


def check(snapshot_path, sqlite_path):
    snapshot = load_snapshot(snapshot_path)
    now = datetime.fromisoformat(snapshot["captured_at_utc"])
    if now.tzinfo is None:
        raise RuntimeError("Snapshot time missing timezone")
    days = [date.fromisoformat(row["slot_date"])
            for row in snapshot["tables"]["psychology_booking_days"]]
    days.extend(date.fromisoformat(row["slot_date"])
                for row in snapshot["tables"]["psychology_booking_holds"])
    days = sorted(set(days))
    compared = 0
    for day in days:
        reference = canonical(expected_windows(snapshot, day, now=now))
        actual = canonical(busy_intervals(sqlite_path, day, day, now=now))
        if reference != actual:
            raise RuntimeError("Occupancy parity differs (one or more days)")
        compared += 1
    return {"status": "PSC_BUSY_PARITY_OK", "days_checked": compared,
            "rows_checked": len(snapshot["tables"]["psychology_booking_holds"]),
            "snapshot_time_utc": now.isoformat(),
            "live_calendar_tested": False, "production_changed": False}


def main():
    if len(sys.argv) != 3:
        raise ValueError("usage: psc_shadow_busy_parity.py ABS_SNAPSHOT ABS_SQLITE")
    snapshot_path, sqlite_path = map(Path, sys.argv[1:])
    if any(not path.is_absolute() or not path.is_file() or path.is_symlink()
           for path in (snapshot_path, sqlite_path)):
        raise ValueError("Only existing, absolute, regular backup files accepted")
    print(json.dumps(check(snapshot_path, sqlite_path), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PSC_BUSY_PARITY_FAILED: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
