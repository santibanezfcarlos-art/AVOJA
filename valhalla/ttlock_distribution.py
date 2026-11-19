"""Opt-in individual delivery of a saved daily TTLock code.

Every recipient gets their own private email. Each attempt has a durable,
unique audit record; ambiguous outcomes are not automatically retried.
No new lock passcodes are ever requested by this module.
"""
import logging
import os
import sqlite3
from datetime import datetime
from time import sleep
from zoneinfo import ZoneInfo

from mailer import EmailDelivery, EmailDeliveryError
from ttlock_daily import AttemptLedger, decrypt_code, email_content
from ttlock_email_html import access_email_html


LOG = logging.getLogger("valhalla.ttlock")


def _initialize(db_path):
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS ttlock_individual_delivery (
            issue_day TEXT NOT NULL,
            recipient TEXT NOT NULL,
            booking_day TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            provider_message_id TEXT,
            http_status INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(issue_day,recipient)
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(ttlock_individual_delivery)")}
        for name, definition in (("provider_message_id", "TEXT"), ("http_status", "INTEGER"),
                                 ("attempts", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                db.execute(f"ALTER TABLE ttlock_individual_delivery ADD COLUMN {name} {definition}")


def _send_one(db_path, issue_day, booking_day, recipient, subject, message, mailer, html=None):
    # Reserve before delivery to protect against restarts / concurrent workers.
    with sqlite3.connect(db_path, timeout=10) as db:
        result = db.execute("""INSERT OR IGNORE INTO ttlock_individual_delivery
            (issue_day,recipient,booking_day,status,created_at)
            VALUES (?,?,?,?,?)""",
            (issue_day,recipient.casefold(),booking_day,"sending",
             datetime.now(ZoneInfo("America/Santiago")).isoformat()))
        if result.rowcount != 1:
            return "already_attempted"
    # Leave room for other transactional traffic on the same provider account.
    sleep(0.25)
    idempotency_key = "ttlock-individual-" + issue_day + "-" + recipient.casefold()
    provider_message_id = None
    http_status = None
    for attempt, retry_delay in enumerate((1, 2, None), start=1):
        try:
            provider_message_id = mailer.send_message(
                recipient, subject, message, idempotency_key=idempotency_key, html=html)
        except EmailDeliveryError as error:
            http_status = error.http_status
            if http_status != 429:
                rejected = (http_status is not None and 400 <= http_status < 500
                            and http_status not in (408, 409))
                status = "rejected" if rejected else "uncertain"
                break
            status = "rate_limited"
            if retry_delay is not None:
                delay = max(retry_delay, error.retry_after or 0)
                if delay > 30:
                    break
                sleep(delay)
        except Exception:
            http_status = None
            # The provider may have accepted the message before the failure.
            status = "uncertain"
            break
        else:
            status = "accepted"
            http_status = None
            break
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("""UPDATE ttlock_individual_delivery SET status=?, provider_message_id=?, http_status=?, attempts=?
            WHERE issue_day=? AND recipient=?""",
            (status, provider_message_id, http_status, attempt, issue_day, recipient.casefold()))
    return status


def distribute_saved(db_path, issue_key, issued_at, encrypted_code, *, mailer=None):
    """Send a previously saved code without generating a new TTLock passcode."""
    from datetime import timezone, timedelta
    if os.environ.get("TTLOCK_DISTRIBUTION_ENABLED","").lower() != "true":
        return "disabled"
    if issued_at.tzinfo is None:
        return "invalid_date"
    local_issue = issued_at.astimezone(ZoneInfo("America/Santiago"))
    # A new code starts at 05:00 on booking day; older 22/23h tests
    # use their issue date and target the following booking day.
    booking_date = local_issue.date() if local_issue.hour == 5 else local_issue.date() + timedelta(days=1)
    end = datetime(booking_date.year,booking_date.month,booking_date.day,23,tzinfo=ZoneInfo("America/Santiago"))
    if datetime.now(timezone.utc) >= end:
        return "expired"
    from ttlock_recipients import recipients_for_day
    booking_day, booked = booking_date, recipients_for_day(db_path, booking_date)
    main = os.environ.get("TTLOCK_DISTRIBUTION_TO","contacto@valhallaoffice.cl").strip()
    if not main or "@" not in main:
        return "recipient_not_configured"
    original = os.environ["TTLOCK_EMAIL_TO"].strip().casefold()
    recipients = []
    seen = set()
    for email in [main]+booked:
        value = email.strip()
        normalized = value.casefold()
        if not value or normalized in seen or (normalized == original and normalized != main.casefold()):
            continue
        recipients.append(value)
        seen.add(normalized)
    if len(recipients)>41:
        return "recipient_limit"
    delivery = mailer or EmailDelivery()
    if not delivery.configured:
        return "email_not_configured"
    code = decrypt_code(encrypted_code)
    subject,message = email_content(code,issued_at)
    html = access_email_html(code,issued_at)
    _initialize(db_path)
    statuses = [
        _send_one(db_path,issue_key,booking_day.isoformat(),email,subject,message,delivery,html=html)
        for email in recipients
    ]
    failures = sum(status not in ("accepted", "already_attempted") for status in statuses)
    if failures:
        LOG.error("TTLock distribution incomplete for %s: %s of %s recipients require review",
                  booking_day, failures, len(recipients))
        return "partial_or_uncertain"
    if all(s=="already_attempted" for s in statuses):
        return "already_attempted"
    return "accepted"


def distribute(db_path, issue_day, transport=None, *, mailer=None):
    if os.environ.get("TTLOCK_DISTRIBUTION_ENABLED","").lower() != "true":
        return "disabled"
    record = AttemptLedger(db_path).get_existing(issue_day)
    if not record or record["status"] != "emailed" or not record["encrypted_code"]:
        return "code_not_ready"
    issued_at = datetime.fromisoformat(issue_day+"T22:00:00").replace(
        tzinfo=ZoneInfo("America/Santiago"))
    return distribute_saved(db_path, issue_day, issued_at, record["encrypted_code"], mailer=mailer)
