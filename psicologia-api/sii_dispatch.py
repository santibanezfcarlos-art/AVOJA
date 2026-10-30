"""Durable, single-request Chromium startup; never retries a tax submission."""
import logging
import threading
import time
from urllib.parse import urlencode
import billing

SCHEMA='''CREATE TABLE IF NOT EXISTS sii_render_dispatch (
 order_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL, state TEXT NOT NULL,
 started_at REAL NOT NULL, updated_at TEXT NOT NULL, http_status INTEGER);'''


def mark(con, order, dispatch_id, state, http=None):
    con.execute("UPDATE sii_render_dispatch SET state=?,updated_at=?,http_status=? WHERE order_id=? AND dispatch_id=?",
                (state,billing.now(),http,order,dispatch_id))


def run(order, dispatch_id, ticket, actor, db_path):
    import sii_canary as sii
    # Do not retain Flask request objects in a background thread.
    try:
        base,secret=sii._visual_service()
        status,_=sii._server_request(base+'/api/internal/start',secret,{'ticket':ticket},timeout=90)
        state='accepted' if status==202 else 'unknown'
    except Exception:
        status=None
        state='unknown'
    with billing.db() as con:
        con.executescript(SCHEMA)
        mark(con,order,dispatch_id,state,status)
        con.execute("INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)",
                    (order,'render_sii_emission_started' if state=='accepted' else 'render_sii_start_unconfirmed',billing.now(),actor))
    logging.getLogger('sii_dispatch').warning('SII_RENDER_DISPATCH state=%s http=%s',state,status)
    if state!='accepted':
        reconcile_preclaim(order)


def reconcile_preclaim(order, browser=None):
    """Release only startup failures that have no durable SII claim or document."""
    import sii_canary as sii
    from sii_post_issue import SCHEMA as RECEIPTS
    with billing.db() as con:
        con.executescript(SCHEMA)
        con.executescript(sii.SCHEMA)
        con.executescript(RECEIPTS)
        q=con.execute("SELECT status FROM sii_issuance_queue WHERE order_id=?",(order,)).fetchone()
        job=con.execute("SELECT state,started_at FROM sii_render_dispatch WHERE order_id=?",(order,)).fetchone()
        if not q or q['status']!='processing' or sii.recorded_attempt(con,order):return False
        if job and job['state']=='connecting' and time.time()-job['started_at']<120:return False
    if browser is None:
        try:
            base,secret=sii._visual_service()
            _,browser=sii._server_request(base+'/api/internal/status?'+urlencode({'order_id':order}),secret,timeout=90)
        except Exception:return False
    if browser.get('state') not in ('not_running','failed','expired','blocked','prepared','idle'):return False
    with billing.db() as con:
        con.execute('BEGIN IMMEDIATE')
        if sii.recorded_attempt(con,order):return False
        if con.execute('SELECT 1 FROM sii_receipts WHERE order_id=?',(order,)).fetchone():return False
        row=con.execute('SELECT environment,payment_status,invoice_status,folio FROM billing_orders WHERE order_id=?',(order,)).fetchone()
        if (not row or row['environment']!='production' or row['payment_status']!='approved'
            or row['invoice_status']!='pending' or row['folio']):return False
        changed=con.execute("UPDATE sii_issuance_queue SET status='awaiting_issuer',updated_at=? WHERE order_id=? AND status='processing'",
                            (billing.now(),order))
        if changed.rowcount!=1:return False
        con.execute("UPDATE sii_render_dispatch SET state='not_started',updated_at=? WHERE order_id=?",(billing.now(),order))
        con.execute('INSERT INTO billing_audit(order_id,action,created_at,actor) VALUES (?,?,?,?)',
                    (order,'sii_preclaim_start_failure_rearmed',billing.now(),'verified_browser_status'))
    logging.getLogger('sii_dispatch').warning('SII_PRECLAIM_RECOVERY state=awaiting_issuer order_suffix=%s no_claim=True no_receipt=True',order[-6:])
    return True


def recover_stalled_preclaims():
    """Startup maintenance only: never starts a browser or emits a document."""
    import sii_canary as sii
    if not billing.configured():return
    with billing.db() as con:
        con.executescript(SCHEMA)
        con.executescript(sii.SCHEMA)
        rows=con.execute("""SELECT q.order_id FROM sii_issuance_queue q JOIN billing_orders o ON o.order_id=q.order_id
            WHERE q.status='processing' AND o.environment='production' AND o.payment_status='approved'
            AND o.invoice_status='pending' AND o.folio IS NULL
            AND NOT EXISTS(SELECT 1 FROM sii_issuance_attempts a WHERE a.order_id=q.order_id)
            AND NOT EXISTS(SELECT 1 FROM sii_first_canary a WHERE a.order_id=q.order_id) LIMIT 50""").fetchall()
    for row in rows:
        reconcile_preclaim(row['order_id'])
