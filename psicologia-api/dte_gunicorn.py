"""Optional Gunicorn config for the existing disk-backed API (not pilot).
Start: gunicorn -c dte_gunicorn.py main:app --bind 0.0.0.0:$PORT
Disabled by default. SQLite leases permit multiple processes safely.
"""
import os
import threading


def post_worker_init(worker):
    if os.getenv('PSC_DTE_MODE','disabled') not in ('integration','production'):
        return
    stop=threading.Event()
    worker.dte_stop=stop
    def run():
        from dte import process_one
        from dte_mail import deliver_one
        while not stop.is_set():
            try:
                process_one()
                deliver_one()
            except Exception as exc:
                worker.log.warning('DTE worker deferred: %s',type(exc).__name__)
            stop.wait(30)
    threading.Thread(target=run,name='dte-outbox',daemon=True).start()


def worker_exit(server,worker):
    if hasattr(worker,'dte_stop'): worker.dte_stop.set()
