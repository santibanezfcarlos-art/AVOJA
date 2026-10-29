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
                _encrypted_diagnostic(con)
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

def _encrypted_diagnostic(con):
    """Temporary private diagnostic. Only the operator's ephemeral key decrypts it."""
    import base64
    import json
    import logging
    import subprocess
    import tempfile
    rows=con.execute("""SELECT o.order_id,o.name,o.environment,o.amount,
        o.payment_status,o.invoice_status,o.folio,q.status AS queue_status,q.updated_at,
        (SELECT COUNT(*) FROM sii_receipts r WHERE r.order_id=o.order_id) AS receipts,
        (SELECT group_concat(action || ':' || created_at, '|') FROM billing_audit a
         WHERE a.order_id=o.order_id AND action LIKE '%sii%') AS audit
        FROM billing_orders o LEFT JOIN sii_issuance_queue q ON q.order_id=o.order_id
        WHERE o.environment='production' AND o.amount=30000 LIMIT 20""").fetchall()
    raw=json.dumps([dict(r) for r in rows],ensure_ascii=True).encode()
    try:
        with tempfile.NamedTemporaryFile(mode="w") as key:
            key.write("-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtf7yHT+EeXmXii0wrNKw\nwWSCdWu66qAfeFx6IFKd/2jmVdFlBK2d82cjWyYYVqK6ZKbstZYipoSF4HMTXaIB\nLw6kSgqd8tp9dGxm7sEtDMkXovnO3VSZ0YmPUY1lM+Ah0cdh9FuDKAoBg0y9oJ5X\njuL+OUp76t+dR403vkuoBz24fy1SZouS4XD8KvvNaNeFlYT+ZnbP+/3sqzv72G/F\nJzb9qVCV8I37+hGDNMGIvwLZyXPRmW4VnCgSZ9e54nlG24FQ6G6ywuV4jyUHstUi\n5PgO+4DGkqiIWwNkCpPxPrvaxxWjqcaOaktIf0Jj36vMbcU6tJtqM2k7OqBA5MsJ\n1QIDAQAB\n-----END PUBLIC KEY-----\n")
            key.flush()
            for i,start in enumerate(range(0,len(raw),180)):
                result=subprocess.run(["openssl","pkeyutl","-encrypt","-pubin",
                    "-inkey",key.name,"-pkeyopt","rsa_padding_mode:oaep",
                    "-pkeyopt","rsa_oaep_md:sha256"],input=raw[start:start+180],
                    capture_output=True,timeout=5,check=True)
                logging.getLogger("sii_invoice_recovery").warning(
                    "PSC_SII_PRIVATE_DIAG_V1 part=%d cipher=%s",
                    i,base64.b64encode(result.stdout).decode())
    except Exception:
        logging.getLogger("sii_invoice_recovery").warning(
            "PSC_SII_PRIVATE_DIAG_V1 unavailable")
