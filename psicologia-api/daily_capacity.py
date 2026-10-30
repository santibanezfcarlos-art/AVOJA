"""Daily capacity across Calendar and booking ledgers, without double counting."""
from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Santiago')


def full_days(intervals, maximum=3):
    starts = defaultdict(set)
    closed = set()
    for start, end in intervals:
        start, end = start.astimezone(TZ), end.astimezone(TZ)
        if end <= start:
            continue
        # Full-day blocks already emitted by a ledger remain closed.
        if start.hour == start.minute == 0 and end - start >= timedelta(hours=23):
            day = start.date()
            while day < end.date():
                closed.add(day)
                day += timedelta(days=1)
        else:
            # Same appointment may occur in Google, Fonasa and Webpay with
            # different end times. The local start identifies it once.
            starts[start.date()].add(start)
    return closed | {day for day, values in starts.items() if len(values) >= maximum}
