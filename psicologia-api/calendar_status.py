"""Private, read-only availability diagnostics. No tokens or event details."""
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Blueprint, current_app, jsonify, redirect, request
import billing

bp = Blueprint('calendar_status', __name__)
_lock = threading.Lock()
_probe_lock = threading.Lock()
_last = {'state': 'unknown', 'checked_at': None, 'http_status': None}
_last_at = 0.0
_last_probe = 0.0


def record(response):
    """Record only health, never dates, occupied slots or patient information."""
    global _last_at
    if request.path != '/api/availability':
        return response
    # Bad caller parameters do not indicate an outage.
    if response.status_code in (400, 404, 405):
        return response
    data = response.get_json(silent=True) or {}
    healthy = (response.status_code == 200 and data.get('status') == 'ok'
               and data.get('configured') is True
               and data.get('duration_minutes') in (45, 60, 90)
               and isinstance(data.get('blocked'), dict) and bool(data['blocked']))
    with _lock:
        _last.update(state='ready' if healthy else 'unavailable',
                     checked_at=datetime.now(ZoneInfo('America/Santiago')).isoformat(timespec='seconds'),
                     http_status=response.status_code)
        _last_at = time.monotonic()
    return response


def snapshot():
    with _lock:
        data = dict(_last)
        if _last_at and time.monotonic() - _last_at > 600:
            data['state'] = 'stale'
    return data


@bp.after_request
def headers(response):
    return billing.private_headers(response)


@bp.get('/admin/calendar/status')
@billing.private
def status():
    return jsonify(snapshot())


@bp.post('/admin/calendar/check')
@billing.private
def check():
    global _last_probe
    if not _probe_lock.acquire(blocking=False):
        return jsonify(state='checking', checked_at=snapshot()['checked_at']), 202
    try:
        if _last_probe and time.monotonic() - _last_probe < 60:
            return jsonify(snapshot())
        _last_probe = time.monotonic()
        app = current_app._get_current_object()
        day = (datetime.now(ZoneInfo('America/Santiago')) + timedelta(days=1)).date().isoformat()
        # Call the same read-only view directly: no HTTP call back into this
        # single-worker server, and no booking/payment/mail side effects.
        from main import availability
        with app.test_request_context('/api/availability', query_string={'from': day, 'to': day, 'duration': '45'}):
            try:
                response = app.make_response(availability())
            except Exception:
                response = app.make_response((jsonify(status='unavailable'), 503))
            record(response)
        return jsonify(snapshot())
    finally:
        _probe_lock.release()


@bp.get('/admin/calendar/reconnect')
@billing.private
def reconnect():
    # Preserve the existing OAuth setup gate, callback, state cookie and scope.
    # No credential is ever placed in a URL or returned to this panel.
    return redirect('https://psicologia-retenciones-piloto.onrender.com/oauth/google/start', code=302)
