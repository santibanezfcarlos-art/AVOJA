"""Opt-in TTLock daily worker. Disabled unless TTLOCK_ENABLED=true.

On an uncertain TTLock API result, never automatically create a second code.
No passcodes or API secrets are logged or stored in the database.
"""
import logging
import os
import threading
from datetime import datetime, timezone

from ttlock_daily import AttemptLedger, TTLockClient, TTLockError, validity_window, email_content
from mailer import EmailDelivery, EmailDeliveryError

LOG = logging.getLogger("valhalla.ttlock")


def run_daily_once(db_path, *, now=None, client=None, mailer=None):
    """One controlled attempt. Returns a status; no implicit retries."""
    now = now or datetime.now(timezone.utc)
    start, end = validity_window(now)
    local_now = now.astimezone(start.tzinfo)
    if local_now < start:
        return "before_schedule"
    # Limit late catch-up to the evening of the scheduled local date.
    if local_now.date() != start.date():
        return "outside_window"

    ledger = AttemptLedger(db_path)
    ledger.initialize()
    day = start.date().isoformat()
    if not ledger.claim(day):
        return "already_claimed"

    try:
        client = client or TTLockClient.from_environment()
        token = client.authenticate()
        details = client.details(token)
        # The API can succeed while its response is lost. Never blindly retry.
        code, code_id = client.generate(token, start, end, details["keyboardPwdVersion"])
    except Exception:
        ledger.mark(day, "uncertain", error_category="ttlock_failure")
        LOG.exception("TTLock attempt uncertain on %s; manual check required", day)
        return "uncertain"

    ledger.mark(day, "created", code_id=code_id)
    try:
        mailer = mailer or EmailDelivery()
        recipient = os.environ["TTLOCK_EMAIL_TO"]
        subject, message = email_content(code, start)
        mailer.send_message(recipient, subject, message,
                            idempotency_key="ttlock-" + str(client.lock_id) + "-" + day)
    except Exception:
        ledger.mark(day, "email_failed", error_category="email_failure")
        LOG.exception("TTLock code created on %s but email failed; manual action required", day)
        return "email_failed"

    ledger.mark(day, "emailed", code_id=code_id)
    LOG.info("TTLock daily email accepted by provider on %s", day)
    return "emailed"


def start_worker(db_path, stop_event=None):
    """Start optional in-process scheduler; caller retains stop_event."""
    stop_event = stop_event or threading.Event()
    if os.environ.get("TTLOCK_ENABLED", "").lower() != "true":
        return None
    required = ["TTLOCK_CLIENT_ID", "TTLOCK_CLIENT_SECRET", "TTLOCK_USERNAME",
                "TTLOCK_PASSWORD_MD5", "TTLOCK_LOCK_ID", "TTLOCK_EMAIL_TO"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        LOG.error("TTLock disabled: missing variables %s", ", ".join(missing))
        return None

    def loop():
        last_observed_day = None
        while not stop_event.is_set():
            now = datetime.now(timezone.utc)
            start, _ = validity_window(now)
            if now >= start.astimezone(timezone.utc):
                day = start.date().isoformat()
                if day != last_observed_day:
                    last_observed_day = day
                    run_daily_once(db_path, now=now)
            stop_event.wait(30)

    thread = threading.Thread(target=loop, daemon=True, name="ttlock-daily")
    thread.start()
    return thread
