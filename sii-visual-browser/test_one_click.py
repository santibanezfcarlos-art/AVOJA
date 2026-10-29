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

    def test_unknown_mode_cannot_use_privileged_background_start(self):
        value=self.data();value['mode']='issue_unrestricted'
        with patch('app._decode_ticket',return_value=value):
            response=self.client.post('/api/internal/start',json={'ticket':'fixture'},
                         headers={'X-PSC-Visual-Bridge':SECRET})
        self.assertEqual(response.status_code,403)

if __name__=='__main__':unittest.main()
