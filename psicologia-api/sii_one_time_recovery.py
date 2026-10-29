"""One-time, auditable recovery of Giovanni's verified unissued SII purchase.

Operator confirmed on 2026-10-29 that there is no $30,000 document on eBoleta.
This recovery does NOT issue any document, call Transbank, or send any email.
It is enabled only for this precisely scoped maintenance event.
"""
import os
import sqlite3
from urllib.parse import urlencode

import billing
import sii_canary
from sii_post_issue import SCHEMA as RECEIPT_SCHEMA

RECOVERY_KEY="sii_giovanni_salas_unissued_20261029_v1"
SCHEMA="""CREATE TABLE IF NOT EXISTS sii_issue_recovery_audit (
 recovery_key TEXT PRIMARY KEY,
 order_id TEXT NOT NULL,
 previous_queue_status TEXT NOT NULL,
 previous_claim_count INTEGER NOT NULL,
 recorded_at TEXT NOT NULL,
 reason TEXT NOT NULL);
"""


def _candidate(con):
    rows=con.execute("""SELECT o.order_id,o.amount,o.invoice_status,o.folio,
                               q.status AS queue_status,q.updated_at
                        FROM billing_orders o
                        LEFT JOIN sii_issuance_queue q ON q.order_id=o.order_id
                        WHERE o.environment='production'
                          AND o.payment_status='approved'
                          AND o.invoice_status='pending'
                          AND o.folio IS NULL
                          AND o.amount=30000
                          AND instr(lower(o.name),'giovanni')>0
                          AND instr(lower(o.name),'salas')>0
                        LIMIT 3""").fetchall()
    return rows


def recover_once():
    """Recover ONLY one verified pending purchase; preserve immutable audit.

    Returns a non-identifying status for operational logs.
    """
    if os.environ.get("PSC_SII_UNLOCK_GIOVANNI_20261029")!="yes":
        return "not_enabled"
    if not billing.configured():
        return "storage_unavailable"
    try:
        with billing.db() as con:
            con.executescript(sii_canary.SCHEMA)
            con.executescript(RECEIPT_SCHEMA)
            con.executescript(SCHEMA)
            # Do not hold a write lock while asking Render whether Chromium
            # is still running; this would block all payment/booking writes.
            if con.execute("SELECT 1 FROM sii_issue_recovery_audit WHERE recovery_key=?",
                           (RECOVERY_KEY,)).fetchone():
                return "already_recovered"
            matches=_candidate(con)
            if len(matches)!=1:
                # Non-identifying diagnostics only; never relax recovery guards.
                import logging
                rows=con.execute("""SELECT o.environment,o.payment_status,o.invoice_status,
                    o.folio IS NOT NULL AS has_folio,q.status AS queue_status,
                    COUNT(*) AS count
                    FROM billing_orders o LEFT JOIN sii_issuance_queue q ON q.order_id=o.order_id
                    WHERE instr(lower(o.name),'giovanni')>0
                    GROUP BY o.environment,o.payment_status,o.invoice_status,
                             o.folio IS NOT NULL,q.status""").fetchall()
                logging.getLogger("sii_invoice_recovery").warning(
                    "PSC_SII_RECOVERY_DIAGNOSTIC matches=%d states=%s",
                    len(matches),[dict(r) for r in rows])
                return "no_unique_pending_match"
            target=matches[0]
            order_id=target["order_id"]
            claimed=con.execute("SELECT 1 FROM sii_issuance_attempts WHERE order_id=?",
                                (order_id,)).fetchone()
            legacy=con.execute("SELECT 1 FROM sii_first_canary WHERE order_id=?",
                               (order_id,)).fetchone()
            claim_count=int(bool(claimed))+int(bool(legacy))
            receipt=con.execute("SELECT 1 FROM sii_receipts WHERE order_id=?",
                                (order_id,)).fetchone()
            if receipt:
                return "document_record_exists_no_reset"
            status=target["queue_status"]
            if status=="awaiting_issuer" and claim_count==0:
                return "already_available"
            if status not in ("processing","reconciliation") and claim_count==0:
                return "not_a_blocked_attempt"
            # Never recover a historical order outside the failed 29 Oct trial.
            updated=str(target["updated_at"] or "")
            evidence=con.execute("""SELECT 1 FROM billing_audit
                  WHERE order_id=? AND created_at LIKE '2026-10-29%'
                  AND action IN ('render_sii_emission_started','sii_canary_final_claimed')
                  LIMIT 1""",(order_id,)).fetchone()
            if not evidence or not updated.startswith("2026-10-29"):
                return "attempt_date_not_verified"
            # First check that the Render browser is not still performing
            # a submission. Only a terminated attempt may be reset.
            try:
                base,secret=sii_canary._visual_service()
                _,browser=sii_canary._server_request(
                    base+"/api/internal/status?"+urlencode({"order_id":order_id}),
                    secret)
            except Exception:
                return "browser_status_unavailable"
            state=browser.get("state")
            stage=browser.get("stage","")
            if state in ("starting","open") and not (
                isinstance(stage,str) and
                stage in ("canary_clicked_reconciliation",
                          "canary_attempt_reconciliation",
                          "canary_issued_report_reconciliation")
            ):
                return "browser_still_active"
            if state in ("starting","open"):
                return "browser_requires_shutdown"
            con.execute("BEGIN IMMEDIATE")
            # Re-check all material conditions under the write lock: a late
            # receipt callback may have arrived during the browser status call.
            if con.execute("SELECT 1 FROM sii_issue_recovery_audit WHERE recovery_key=?",
                           (RECOVERY_KEY,)).fetchone():
                return "already_recovered"
            current=_candidate(con)
            if len(current)!=1 or current[0]["order_id"]!=order_id or current[0]["queue_status"]!=status:
                return "state_changed_no_reset"
            if con.execute("SELECT 1 FROM sii_receipts WHERE order_id=?",(order_id,)).fetchone():
                return "document_record_exists_no_reset"
            newer_count=int(bool(con.execute("SELECT 1 FROM sii_issuance_attempts WHERE order_id=?",
                                             (order_id,)).fetchone()))+int(bool(con.execute(
                "SELECT 1 FROM sii_first_canary WHERE order_id=?",(order_id,)).fetchone()))
            if newer_count!=claim_count:
                return "state_changed_no_reset"
            now=billing.now()
            con.execute("""INSERT INTO sii_issue_recovery_audit
                 (recovery_key,order_id,previous_queue_status,previous_claim_count,
                  recorded_at,reason)
                 VALUES (?,?,?,?,?,'user_verified_no_sii_dte41_30000_on_2026_10_29')""",
                 (RECOVERY_KEY,order_id,status or "missing",claim_count,now))
            con.execute("DELETE FROM sii_issuance_attempts WHERE order_id=?",(order_id,))
            con.execute("DELETE FROM sii_first_canary WHERE order_id=?",(order_id,))
            updated=con.execute("""UPDATE sii_issuance_queue
                       SET status='awaiting_issuer',updated_at=?
                       WHERE order_id=? AND status IN ('processing','reconciliation','awaiting_issuer')""",
                       (now,order_id))
            if updated.rowcount!=1:
                raise RuntimeError("queue_recovery_conflict")
            con.execute("""INSERT INTO billing_audit(order_id,action,created_at,actor)
                           VALUES (?,?,?,'operator_verified_2026_10_29')""",
                        (order_id,"sii_verified_no_issue_rearmed_once",now))
            return "unblocked"
    except (sqlite3.Error,RuntimeError,OSError):
        return "recovery_failed"
