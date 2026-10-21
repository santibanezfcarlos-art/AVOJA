"""Daily access email BCC distribution. Opt-in only, never creates codes."""
import json
import os
import sqlite3
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from ttlock_daily import AttemptLedger, decrypt_code, email_content
from ttlock_recipients import recipients_for_tomorrow


def distribute(db_path, issue_day, transport=None):
    if os.environ.get("TTLOCK_DISTRIBUTION_ENABLED") != "true":
        return "disabled"
    record = AttemptLedger(db_path).get_existing(issue_day)
    if not record or record["status"] != "emailed" or not record["encrypted_code"]:
        return "code_not_ready"
    issued_at = datetime.fromisoformat(issue_day + "T22:00:00").replace(
        tzinfo=ZoneInfo("America/Santiago"))
    day, recipients = recipients_for_tomorrow(db_path, issued_at)
    owner = os.environ.get("TTLOCK_DISTRIBUTION_TO", "contacto@valhallaoffice.cl").strip()
    if not owner or "@" not in owner:
        return "recipient_not_configured"
    # Do not repeat the primary distribution address or the original TTLock
    # fallback inbox in BCC.
    excluded = {owner.casefold(), os.environ["TTLOCK_EMAIL_TO"].strip().casefold()}
    recipients = [r for r in recipients if r.strip().casefold() not in excluded]
    if not recipients:
        return "no_recipients"
    if len(recipients) > 40:
        return "recipient_limit"
    code = decrypt_code(record["encrypted_code"])
    subject, message = email_content(code, issued_at)
    # An attempt record is committed before sending to avoid double submissions.
    with sqlite3.connect(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ttlock_distribution_events (
            issue_day TEXT PRIMARY KEY, booking_day TEXT, status TEXT, recipient_count INTEGER)""")
        cursor = db.execute("""INSERT OR IGNORE INTO ttlock_distribution_events
            VALUES (?,?,?,?)""", (issue_day, day.isoformat(), "pending", len(recipients)))
        if cursor.rowcount == 0:
            return "already_attempted"
    payload = {"from":os.environ["VALHALLA_EMAIL_FROM"],"to":[owner],
               "bcc":recipients,"subject":subject,"text":message}
    request = Request("https://api.resend.com/emails",
        data=json.dumps(payload).encode(),method="POST",
        headers={"Authorization":"Bearer "+os.environ["VALHALLA_RESEND_API_KEY"],
                 "Content-Type":"application/json",
                 "Idempotency-Key":"ttlock-distribution-"+issue_day})
    try:
        with (transport or urlopen)(request,timeout=15) as response:
            result=json.load(response)
            if not isinstance(result,dict) or not result.get("id"):
                raise ValueError("Missing provider receipt")
        status="accepted"
    except Exception:
        status="uncertain"
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE ttlock_distribution_events SET status=? WHERE issue_day=?",
                   (status,issue_day))
    return status
