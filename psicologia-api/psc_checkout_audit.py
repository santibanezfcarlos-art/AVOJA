"""Isolated PSC checkout concurrency and restart-durability smoke tests.

No Transbank requests, no Google Calendar writes, no email sending, no SII work,
no production ledger reads/writes during concurrency tests.
"""
import concurrent.futures
import os
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def run():
    import production_checkout as checkout
    from billing import configured, db
    if not configured() or not os.path.ismount("/var/data"):
        raise RuntimeError("Existing production persistent storage unavailable")
    now = datetime.now(ZoneInfo("America/Santiago"))
    day = now.date() + timedelta(days=5)
    start = datetime(day.year, day.month, day.day, 11, 0,
                     tzinfo=ZoneInfo("America/Santiago"))
    end = start + timedelta(minutes=45)
    outcomes = []
    ready = threading.Barrier(8)
    with tempfile.TemporaryDirectory(prefix="psc-audit-") as directory:
        path = str(Path(directory) / "isolated-checkout.sqlite3")
        init = sqlite3.connect(path, timeout=15)
        try:
            init.executescript(checkout.SCHEMA)
            init.commit()
        finally:
            init.close()

        def contender(n):
            ready.wait(timeout=10)
            connection = sqlite3.connect(path, timeout=20)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    checkout.claim(connection, start, end)
                    key = "TEST" + uuid.uuid4().hex
                    connection.execute(
                        """INSERT INTO production_payments(
                            order_id,request_id,session_id,receipt,hold_id,service,
                            starts_at,ends_at,expires_at,amount,state,rebook_token)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (key, "req"+key, "session"+key, "receipt"+key,
                         "hold"+key, "psicoterapia", start.isoformat(),
                         end.isoformat(), 2147483647.0, 30000, "CREATING",
                         "rebook"+key))
                    connection.commit()
                    return "accepted"
                except ValueError:
                    connection.rollback()
                    return "rejected"
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(contender, range(8)))
        if outcomes.count("accepted") != 1 or outcomes.count("rejected") != 7:
            raise AssertionError("Concurrent slot claim did not serialize")

        # Reopen a new physical connection as a distinct process would after
        # a service restart. Verify the pending reservation survived.
        after = sqlite3.connect(path, timeout=10)
        after.row_factory = sqlite3.Row
        try:
            row = after.execute(
                "SELECT order_id,state FROM production_payments").fetchone()
            if not row or row["state"] != "CREATING":
                raise AssertionError("Durable pending state missing on reopen")
            after.execute(
                "UPDATE production_payments SET state='AUTHORIZED' WHERE order_id=?",
                (row["order_id"],))
            after.commit()
        finally:
            after.close()
        after = sqlite3.connect(path, timeout=10)
        try:
            state = after.execute(
                "SELECT state FROM production_payments").fetchone()[0]
            if state != "AUTHORIZED":
                raise AssertionError("Durable authorized state missing on reopen")
            after.execute(
                """UPDATE production_payments
                   SET state='CONFIRMED',patient_mail=1,professional_mail=1
                   WHERE state='AUTHORIZED'""")
            after.commit()
        finally:
            after.close()
        after = sqlite3.connect(path, timeout=10)
        try:
            row = after.execute(
                "SELECT state,patient_mail,professional_mail FROM production_payments"
            ).fetchone()
            if row != ("CONFIRMED", 1, 1):
                raise AssertionError("Durable confirmed state missing on reopen")
            integrity = after.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise AssertionError("SQLite integrity failure")
        finally:
            after.close()

    # Read-only production diagnostics, aggregate counters ONLY.
    with db() as c:
        statuses = dict(c.execute(
            "SELECT state, count(*) FROM production_payments GROUP BY state"
        ).fetchall())
        pending_mail = c.execute(
            """SELECT COUNT(*) FROM production_payments
               WHERE state='CONFIRMED'
                 AND (patient_mail=0 OR professional_mail=0)"""
        ).fetchone()[0]
        pending_invoice = c.execute(
            """SELECT COUNT(*) FROM billing_orders
               WHERE payment_status='approved' AND invoice_status='pending'"""
        ).fetchone()[0]
        count_paid = c.execute(
            """SELECT COUNT(*) FROM billing_orders
               WHERE environment='production' AND payment_status='approved'"""
        ).fetchone()[0]

    # Inspect only aggregate state/date/amount buckets to identify old test
    # purchases. No order identifiers or patient contact data enter logs.
    with db() as c:
        unresolved = c.execute("""
            SELECT p.state, COALESCE(b.payment_status,'unrecorded'),
                   CASE WHEN p.amount < 1000 THEN 'micro_test_amount' ELSE 'standard_amount' END,
                   COALESCE(substr(b.created_at,1,10),'unknown_date'), COUNT(*)
            FROM production_payments p
            LEFT JOIN billing_orders b USING(order_id)
            WHERE p.state IN ('AUTHORIZED','COMMITTING','READY','RECONCILIATION')
            GROUP BY 1,2,3,4 ORDER BY 1,4
        """).fetchall()
    backlog = ["|".join(str(x) for x in row) for row in unresolved]

    # Signed, read-only production adapters: no payment capture, no event
    # creation, no email delivery or SII invoicing is triggered.
    try:
        health_status = checkout.remote("health", {}).get("state", "INVALID")
    except Exception as exc:
        health_status = "ERROR_" + type(exc).__name__
    try:
        result = checkout.remote("check", {
            "start": start.isoformat(), "end": end.isoformat()
        })
        calendar_status = result.get("state", "INVALID")
    except Exception as exc:
        calendar_status = "ERROR_" + type(exc).__name__

    return {
        "concurrent_accept": 1, "concurrent_reject": 7,
        "durable_reopen": "OK", "integrity_check": "OK",
        "production_sales_configured": checkout.enabled(),
        "production_states": statuses,
        "confirmed_missing_any_email": pending_mail,
        "approved_pending_invoice": pending_invoice,
        "approved_production_orders": count_paid,
        "unresolved_buckets": backlog,
        "fulfillment_health": health_status,
        "calendar_read_state": calendar_status
    }
