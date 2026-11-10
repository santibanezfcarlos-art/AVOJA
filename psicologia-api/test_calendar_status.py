import base64
import os
import sys
import types
import unittest
from unittest.mock import patch
from flask import Flask, jsonify
import calendar_status as health


class CalendarHealthTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(health.bp)
        self.client = self.app.test_client()
        self.auth = {'Authorization': 'Basic ' + base64.b64encode(b'carlos:long-test-password').decode(),
                     'X-Billing-Action': 'manual-invoice'}
        self.env = patch.dict(os.environ, {'PSICOLOGIA_BILLING_ADMIN_PASSWORD': 'long-test-password',
                                          'PSICOLOGIA_BILLING_ADMIN_USER': 'carlos', 'PSICOLOGIA_BILLING_ADMIN_HASH': ''})
        self.env.start()
        self.conf = patch.object(health.billing, 'configured', return_value=True)
        self.conf.start()
        health._last.update(state='unknown', checked_at=None, http_status=None)
        health._last_at = health._last_probe = 0.0

    def tearDown(self):
        self.conf.stop()
        self.env.stop()

    def test_private_routes_do_not_probe_or_redirect_without_auth(self):
        self.assertEqual(self.client.get('/admin/calendar/status').status_code, 401)
        self.assertEqual(self.client.post('/admin/calendar/check').status_code, 401)
        self.assertEqual(self.client.get('/admin/calendar/reconnect').status_code, 401)
        self.assertEqual(health.snapshot()['state'], 'unknown')

    def test_probe_uses_real_contract_but_never_returns_busy_data(self):
        calls = []
        def available():
            calls.append(True)
            return jsonify(status='ok', configured=True, duration_minutes=45,
                           blocked={'tomorrow': ['09:00']})
        with patch.dict(sys.modules, {'main': types.SimpleNamespace(availability=available)}):
            result = self.client.post('/admin/calendar/check', headers=self.auth)
            self.assertEqual(result.json['state'], 'ready')
            self.assertEqual(set(result.json), {'state', 'checked_at', 'http_status'})
            self.client.post('/admin/calendar/check', headers=self.auth)
            self.assertEqual(len(calls), 1)
        self.assertIn('no-store', result.headers['Cache-Control'])

    def test_failure_is_not_healthy_and_no_sensitive_error_is_returned(self):
        def available():
            raise RuntimeError('secret credential must never be returned')
        with patch.dict(sys.modules, {'main': types.SimpleNamespace(availability=available)}):
            r = self.client.post('/admin/calendar/check', headers=self.auth)
        self.assertEqual(r.json['state'], 'unavailable')
        self.assertNotIn('secret', r.text)

    def test_csrf_gate_and_fixed_reconnect_destination(self):
        headers = {'Authorization': self.auth['Authorization']}
        self.assertEqual(self.client.post('/admin/calendar/check', headers=headers).status_code, 403)
        r = self.client.get('/admin/calendar/reconnect', headers=headers)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers['Location'], 'https://psicologia-retenciones-piloto.onrender.com/oauth/google/start')

    def test_stale_and_bad_parameters_are_not_new_healthy_checks(self):
        with self.app.test_request_context('/api/availability'):
            health.record(self.app.make_response((jsonify(error='bad date'), 400)))
        self.assertEqual(health.snapshot()['state'], 'unknown')
        health._last_at = 100
        health._last['state'] = 'ready'
        with patch.object(health.time, 'monotonic', return_value=701):
            self.assertEqual(health.snapshot()['state'], 'stale')


if __name__ == '__main__':
    unittest.main()
