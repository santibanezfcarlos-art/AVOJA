import os
import unittest
from unittest.mock import patch
import app as browser

SECRET='offline-visual-sii-shared-key-for-testing'
ORDER='PSCP'+'C'*18

class OneClickBrowserTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'SII_VISUAL_TICKET_SECRET':SECRET})
        self.env.start()
        self.client=browser.app.test_client()
        browser._used_nonces.clear()
        browser._session.clear()
        browser._session.update(state='idle',browser=None,started=None,stage='idle')

    def tearDown(self):
        self.env.stop()
        browser._session.clear()
        browser._session.update(state='idle',browser=None,started=None,stage='idle')

    def data(self):
        return {'exp':1999999999,'nonce':'nonce-one-click-offline-123456789',
                'preview_order':ORDER,'mode':'issue_canary'}

    def test_internal_start_requires_server_secret(self):
        self.assertEqual(self.client.post('/api/internal/start',json={'ticket':'bad'}).status_code,403)
        self.assertEqual(self.client.get('/api/internal/status?order_id='+ORDER).status_code,403)

    def test_background_start_automatically_approves_when_form_is_ready(self):
        credentials={'rut':'offline','password':'offline',
                     'preview_order':{'order_id':ORDER,'amount':30000}}
        with (patch('app._decode_ticket',return_value=self.data()),
              patch('app._fetch_sii_credentials',return_value=credentials),
              patch('app.threading.Thread') as thread):
            response=self.client.post('/api/internal/start',json={'ticket':'signed-fixture'},
                                      headers={'X-PSC-Visual-Bridge':SECRET})
            self.assertEqual(response.status_code,202,response.text)
            self.assertTrue(browser._session['auto_issue'])
            self.assertFalse(browser._session['issue_confirmed']) # only after preflight
            self.assertEqual(browser._session['order_id'],ORDER)
            self.assertEqual(thread.call_count,2)
            status=self.client.get('/api/internal/status?order_id='+ORDER,
                                   headers={'X-PSC-Visual-Bridge':SECRET})
            self.assertEqual(status.json['state'],'starting')
            self.assertNotIn('password',status.text)
            rejected=self.client.post('/api/internal/start',json={'ticket':'another-fixture'},
                                      headers={'X-PSC-Visual-Bridge':SECRET})
            self.assertEqual(rejected.status_code,403)  # Replay ticket rejected before second session

    def test_preview_ticket_starts_silent_background_without_issue_permission(self):
        value=self.data()
        value["mode"]="preview"
        credentials={"rut":"fake","password":"fake",
                     "preview_order":{"order_id":ORDER,"amount":30000}}
        with (patch("app._decode_ticket",return_value=value),
              patch("app._fetch_sii_credentials",return_value=credentials),
              patch("app.threading.Thread") as thread):
            r=self.client.post("/api/internal/start",
                json={"ticket":"signed-preview-fixture"},
                headers={"X-PSC-Visual-Bridge":SECRET})
        self.assertEqual(r.status_code,202,r.text)
        self.assertEqual(browser._session["mode"],"preview")
        self.assertTrue(browser._session["auto_issue"])
        self.assertFalse(browser._session["issue_confirmed"])
        status=self.client.get("/api/internal/status?order_id="+ORDER,
                               headers={"X-PSC-Visual-Bridge":SECRET})
        self.assertEqual(status.status_code,200)
        self.assertEqual(thread.call_count,2)

    def test_completed_visual_preview_is_reused_and_closed_without_relogin(self):
        old=self.data()
        old['mode']='preview'
        browser._session.update(state='open',mode='preview',order_id=ORDER,
                                preparation='prepared_not_issued',stage='prepared_not_issued',
                                stop=False)
        with (patch('app._decode_ticket',return_value=old),
              patch('app._fetch_sii_credentials') as fetch):
            res=self.client.post('/api/internal/start',
                json={'ticket':'other-signed-preview'},
                headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(res.status_code,202,res.text)
        self.assertTrue(res.json['reused'])
        self.assertEqual(res.json['stage'],'prepared_not_issued')
        self.assertTrue(browser._session['stop'])
        fetch.assert_not_called()

    def test_different_finished_preview_is_released_before_next_order(self):
        value=self.data()
        value['mode']='preview'
        browser._session.update(state='open',mode='preview',order_id='PSCP'+'F'*18,
                                preparation='prepared_not_issued',stage='prepared_not_issued',
                                stop=False)
        credentials={'rut':'fixture','password':'fixture',
                     'preview_order':{'order_id':ORDER,'amount':30000}}
        def complete_old(_seconds):
            self.assertTrue(browser._session['stop'])
            browser._session.update(state='prepared',started=None)
        with (patch('app._decode_ticket',return_value=value),
              patch('app._fetch_sii_credentials',return_value=credentials),
              patch('app.time.sleep',side_effect=complete_old),
              patch('app.threading.Thread')):
            res=self.client.post('/api/internal/start',
                json={'ticket':'new-signed-preview'},
                headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(res.status_code,202,res.text)
        self.assertEqual(browser._session['order_id'],ORDER)
        self.assertFalse(browser._session['issue_confirmed'])
        self.assertEqual(browser._session['mode'],'preview')

    def test_active_real_issue_is_never_stopped(self):
        value=self.data();value['mode']='preview'
        browser._session.update(state='open',mode='issue_canary',
                                order_id='PSCP'+'D'*18,stage='claiming_canary',
                                preparation='prepared_canary_awaiting_approval',stop=False)
        with patch('app._decode_ticket',return_value=value):
            res=self.client.post('/api/internal/start',
                 json={'ticket':'new-preview'},headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(res.status_code,409)
        self.assertFalse(browser._session['stop'])

    def test_credentials_fetch_happens_only_after_http_start_response(self):
        value=self.data();value['mode']='preview'
        with (patch('app._decode_ticket',return_value=value),
              patch('app._fetch_sii_credentials') as fetch,
              patch('app.threading.Thread') as thread):
            res=self.client.post('/api/internal/start',
                json={'ticket':'signed-preview'},
                headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(res.status_code,202,res.text)
        fetch.assert_not_called()
        self.assertEqual(thread.call_args_list[0].kwargs['args'],
                         (browser._session['started'],'signed-preview','preview'))
        self.assertEqual(browser._session['order_id'],ORDER)

    def test_failed_background_credentials_leave_safe_error_state(self):
        value=self.data();value['mode']='preview'
        with (patch('app._decode_ticket',return_value=value),
              patch('app.threading.Thread')):
            res=self.client.post('/api/internal/start',
                json={'ticket':'signed-preview'},
                headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(res.status_code,202)
        started=browser._session['started']
        with (patch('app._fetch_sii_credentials',
                    side_effect=RuntimeError('credential_bridge_missing_purchase')),
              patch('app._decode_ticket',return_value=value)):
            browser._browser_owner(started,'signed-preview','preview')
        self.assertEqual(browser._session['state'],'failed')
        self.assertEqual(browser._session['stage'],'credential_fetch')
        self.assertEqual(browser._session['error'],'credential_bridge_missing_purchase')

    def test_unknown_mode_cannot_use_privileged_background_start(self):
        value=self.data();value['mode']='issue_unrestricted'
        with patch('app._decode_ticket',return_value=value):
            response=self.client.post('/api/internal/start',json={'ticket':'fixture'},
                         headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(response.status_code,403)

if __name__=='__main__':unittest.main()
