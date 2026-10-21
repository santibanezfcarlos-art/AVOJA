"""Opt-in individual delivery of a saved daily TTLock code.

Every recipient gets their own private email. Each attempt has a durable,
unique audit record; ambiguous outcomes are not automatically retried.
No new lock passcodes are ever requested by this module.
"""
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from mailer import EmailDelivery
from ttlock_daily import AttemptLedger, decrypt_code, email_content
from ttlock_email_html import access_email_html
from ttlock_recipients import recipients_for_tomorrow


def _initialize(db_path):
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ttlock_individual_delivery (
            issue_day TEXT NOT NULL,
            recipient TEXT NOT NULL,
            booking_day TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(issue_day,recipient)
        )""")


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
    try:
        # One TO only: recipients never see anyone else's email.
        mailer.send_message(recipient,subject,message,
            idempotency_key="ttlock-individual-"+issue_day+"-"+recipient.casefold(),
            html=html)
    except Exception:
        # Could have been delivered. A human must investigate before retry.
        status = "uncertain"
    else:
        status = "accepted"
    with sqlite3.connect(db_path,timeout=10) as db:
        db.execute("""UPDATE ttlock_individual_delivery SET status=?
            WHERE issue_day=? AND recipient=?""",
            (status,issue_day,recipient.casefold()))
    return status


def distribute_saved(db_path, issue_key, issued_at, encrypted_code, *, mailer=None):
    """Send a previously saved code without generating a new TTLock passcode."""
    from datetime import timezone, timedelta
    if os.environ.get("TTLOCK_DISTRIBUTION_ENABLED","").lower() != "true":
        return "disabled"
    if issued_at.tzinfo is None:
        return "invalid_date"
    end_day = issued_at.astimezone(ZoneInfo("America/Santiago")).date() + timedelta(days=1)
    end = datetime(end_day.year,end_day.month,end_day.day,issued_at.astimezone(ZoneInfo("America/Santiago")).hour,tzinfo=ZoneInfo("America/Santiago"))
    if datetime.now(timezone.utc) >= end:
        return "expired"
    booking_day, booked = recipients_for_tomorrow(db_path, issued_at)
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
    if "uncertain" in statuses:
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
