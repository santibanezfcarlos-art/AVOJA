"""Offline tests for exactly-once unblocking, no tax issuance and no re-send."""
import os
import tempfile
import unittest
from unittest.mock import patch

import billing
import sii_canary
from sii_one_time_recovery import recover_once,RECOVERY_KEY

OID="PSCP"+"A"*18
OTHER="PSCP"+"B"*18


def add_paid(order,name):
    person={"rut":"12345678-5","name":name,"email":"test@example.invalid",
            "address":"Calle de prueba 123","phone":"+56912345678"}
    billing.prepare(order,"psicoterapia","2026-11-14","10:00",30000,person,"production")
    token="X"*40 if order==OID else "Y"*40
    billing.bind_token(order,token)
    billing.record_result(order,token,30000,True,"VD")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{
            "RENDER":"",
            "PSICOLOGIA_BILLING_DB":self.tmp.name+"/billing.db",
            "PSICOLOGIA_BILLING_ADMIN_PASSWORD":"offline-private-password",
            "PSICOLOGIA_BILLING_ADMIN_HASH":"",
            "PSC_SII_UNLOCK_GIOVANNI_20261029":"yes"})
        self.env.start()
        self.target=patch("sii_one_time_recovery.TARGET_ORDER",OID)
        self.target.start()
        add_paid(OID,"Giovanni Salas")
        with billing.db() as con:
            sii_canary.any_claim(con)
            con.execute("UPDATE sii_issuance_queue SET status='reconciliation',updated_at=? WHERE order_id=?",
                        ("2026-10-29T19:47:50-03:00",OID))
            con.execute("""INSERT INTO sii_issuance_attempts
                         (order_id,nonce_hash,state,claimed_at)
                         VALUES (?,?,'reconciliation',?)""",
                        (OID,"test-nonce-hash","2026-10-29T19:47:50-03:00"))
            con.execute("""INSERT INTO billing_audit(order_id,action,created_at,actor)
                         VALUES (?,'sii_canary_final_claimed',?,'sii_browser')""",
                        (OID,"2026-10-29T19:47:50-03:00"))
        self.browser=patch("sii_one_time_recovery.sii_canary._visual_service",
                           return_value=("https://psc-sii-visual-test.onrender.com",
                                         "fake-shared-secret-32-long-please"))
        self.remote=patch("sii_one_time_recovery.sii_canary._server_request",
                          return_value=(200,{"state":"not_running","stage":"not_running"}))
        self.browser.start()
        self.remote.start()

    def tearDown(self):
        self.remote.stop()
        self.browser.stop()
        self.target.stop()
        self.env.stop()
        self.tmp.cleanup()

    def state(self,order=OID):
        with billing.db() as con:
            from sii_one_time_recovery import SCHEMA
            con.executescript(SCHEMA)
            row=con.execute("SELECT invoice_status,folio,payment_status FROM billing_orders WHERE order_id=?",
                            (order,)).fetchone()
            queue=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",
                              (order,)).fetchone()
            claims=con.execute("SELECT COUNT(*) FROM sii_issuance_attempts WHERE order_id=?",
                               (order,)).fetchone()[0]
            journal=con.execute("SELECT COUNT(*) FROM sii_issue_recovery_audit").fetchone()[0]
        return row,queue,claims,journal

    def test_rearms_giovanni_once_without_marking_issued(self):
        self.assertEqual(recover_once(),"unblocked")
        self.assertEqual(recover_once(),"already_recovered")
        row,queue,claims,journal=self.state()
        self.assertEqual(queue["status"],"awaiting_issuer")
        self.assertEqual(row["invoice_status"],"pending")
        self.assertEqual(row["payment_status"],"approved")
        self.assertIsNone(row["folio"])
        self.assertEqual((claims,journal),(0,1))
        with billing.db() as con:
            acts=[a[0] for a in con.execute("SELECT action FROM billing_audit WHERE order_id=?",
                                            (OID,)).fetchall()]
        self.assertIn("sii_verified_no_issue_rearmed_once",acts)
        self.assertIn("sii_canary_final_claimed",acts)

    def test_does_not_touch_another_pending_purchase(self):
        add_paid(OTHER,"Fernando Salas Rodriguez")
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OTHER,))
        self.assertEqual(recover_once(),"unblocked")
        row,queue,claims,journal=self.state(OTHER)
        self.assertEqual(queue["status"],"processing")
        self.assertEqual(journal,1)

    def test_no_reset_if_chromium_still_issuing(self):
        with patch("sii_one_time_recovery.sii_canary._server_request",
                   return_value=(200,{"state":"open","stage":"waiting_sii_receipt"})):
            self.assertEqual(recover_once(),"browser_still_active")
        self.assertEqual(self.state()[1]["status"],"reconciliation")

    def test_ambiguous_browser_status_does_not_rearm(self):
        with patch("sii_one_time_recovery.sii_canary._server_request",
                   side_effect=TimeoutError):
            self.assertEqual(recover_once(),"browser_status_unavailable")
        self.assertEqual(self.state()[1]["status"],"reconciliation")

    def test_document_record_blocks_recovery(self):
        from sii_post_issue import SCHEMA
        with billing.db() as con:
            con.executescript(SCHEMA)
            con.execute("""INSERT INTO sii_receipts(
              order_id,folio,issued_date,amount,recipient,patient_name,detail,
              pdf,sii_state,psc_state,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (OID,"777","2026-10-29",30000,"test@example.invalid",
               "Giovanni Salas","Sesión de psicoterapia breve online",
               None,"pending","pending","2026-10-29"))
        self.assertEqual(recover_once(),"document_record_exists_no_reset")
        self.assertEqual(self.state()[1]["status"],"reconciliation")

    def test_scope_uses_order_not_patient_name(self):
        with billing.db() as con:
            con.execute("UPDATE billing_orders SET name='Fernando Salas Rodriguez' WHERE order_id=?",
                        (OID,))
        self.assertEqual(recover_once(),"unblocked")
        self.assertEqual(self.state()[1]["status"],"awaiting_issuer")

    def test_unapproved_order_is_never_modified(self):
        with billing.db() as con:
            con.execute("UPDATE billing_orders SET payment_status='initiated' WHERE order_id=?",(OID,))
        self.assertEqual(recover_once(),"no_unique_pending_match")
        self.assertEqual(self.state()[1]["status"],"reconciliation")

    def test_dispatch_intent_is_durable_and_not_repeated(self):
        import main
        from sii_one_time_recovery import dispatch_authorized_once
        self.assertEqual(recover_once(),"unblocked")
        with (patch.dict(os.environ,{"PSC_SII_DISPATCH_APPROVED_ORDER":OID}),
              patch("time.sleep"),
              patch("sii_canary.start_emission",return_value=(main.app.response_class('{}',mimetype='application/json'),503)) as start):
            dispatch_authorized_once(main.app)
            dispatch_authorized_once(main.app)
            self.assertEqual(start.call_count,1)

    def test_can_rearm_only_failed_credentials_before_any_sii_claim(self):
        import main
        from sii_one_time_recovery import dispatch_authorized_once
        self.assertEqual(recover_once(),"unblocked")
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OID,))
        with (patch.dict(os.environ,{"PSC_SII_DISPATCH_APPROVED_ORDER":OID}),
              patch("time.sleep"),
              patch("sii_one_time_recovery.sii_canary._server_request",return_value=(200,{"state":"failed","stage":"credential_fetch"})),
              patch("sii_canary.start_emission",return_value=(main.app.response_class('{}',mimetype='application/json'),503)) as start):
            dispatch_authorized_once(main.app)
            self.assertEqual(start.call_count,1)
        self.assertEqual(self.state()[1]["status"],"awaiting_issuer")

    def test_never_rearms_failure_after_chromium_launch(self):
        import main
        from sii_one_time_recovery import dispatch_authorized_once
        self.assertEqual(recover_once(),"unblocked")
        with billing.db() as con:
            con.execute("UPDATE sii_issuance_queue SET status='processing' WHERE order_id=?",(OID,))
        with (patch.dict(os.environ,{"PSC_SII_DISPATCH_APPROVED_ORDER":OID}),
              patch("sii_one_time_recovery.sii_canary._server_request",return_value=(200,{"state":"failed","stage":"waiting_sii_receipt"})),
              patch("sii_canary.start_emission") as start):
            dispatch_authorized_once(main.app)
            start.assert_not_called()
        self.assertEqual(self.state()[1]["status"],"processing")

    def test_archives_the_full_previous_claim(self):
        import json
        self.assertEqual(recover_once(),"unblocked")
        with billing.db() as con:
            row=con.execute("SELECT row_json FROM sii_recovered_claim_archive WHERE source_table='sii_issuance_attempts'").fetchone()
        self.assertEqual(json.loads(row[0])["nonce_hash"],"test-nonce-hash")

    def test_not_enabled(self):
        with patch.dict(os.environ,{"PSC_SII_UNLOCK_GIOVANNI_20261029":"no"}):
            self.assertEqual(recover_once(),"not_enabled")
        self.assertEqual(self.state()[1]["status"],"reconciliation")


if __name__=="__main__":
    unittest.main()

