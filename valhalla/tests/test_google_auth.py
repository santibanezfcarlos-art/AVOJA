import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from app import Store, ADMIN_EMAIL, Problem
from google_auth import GoogleOAuth, GoogleError, begin, consume, authenticate, validate_identity


class GoogleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name)/'google.sqlite3')
        self.person = {'sub':'google-123','email':'professional@example.com','name':'Profesional Google','email_verified':True}

    def register(self):
        return self.store.register({'name':'Nombre original','email':self.person['email'],'password':'PasswordForTests123','specialty':'Psicología'})

    def test_new_signup_verified_email_but_not_identity_and_no_admin(self):
        token, result = authenticate(self.store,self.person)
        user = self.store.session(token)
        self.assertEqual(result,'profile_required')
        self.assertEqual(user['role'],'professional')
        self.assertEqual(user['email_verified'],1)
        with self.store.connection() as c:
            self.assertFalse(self.store.identity_verified(c,user['id']))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0],1)
            with self.assertRaises(Problem): self.store.professional(c,user['id'])
        second, _ = authenticate(self.store,self.person)
        self.assertEqual(self.store.session(second)['id'],user['id'])
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0],1)

    def test_existing_email_requires_authenticated_link_preserves_account(self):
        uid = self.register()
        with self.assertRaises(GoogleError) as error: authenticate(self.store,self.person)
        self.assertEqual(error.exception.result,'link_required')
        old = self.store.login({'email':self.person['email'],'password':'PasswordForTests123'})
        with self.store.connection(True) as c:
            c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at) VALUES(?,10,7,100,'paid','2026-10-19')",(uid,))
        new,result = authenticate(self.store,self.person,old)
        user = self.store.session(new)
        self.assertEqual(user['id'],uid)
        self.assertEqual(user['name'],'Nombre original')
        self.assertEqual(result,'success')
        self.assertEqual(self.store.state(user)['packages'][0]['remaining'],7)
        self.assertTrue(self.store.state(user)['user']['google_linked'])

    def test_no_link_with_revoked_session_or_other_email(self):
        self.register()
        for session in ('revoked',''):
            with self.assertRaises(GoogleError): authenticate(self.store,self.person,session)
        token = self.store.login({'email':self.person['email'],'password':'PasswordForTests123'})
        with self.assertRaises(GoogleError): authenticate(self.store,dict(self.person,email='other@example.com'),token)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM google_accounts').fetchone()[0],0)

    def test_reserved_admin_unverified_email_and_disabled_user_rejected(self):
        for person in (dict(self.person,email=ADMIN_EMAIL),dict(self.person,email_verified=False),dict(self.person,email_verified='true'),dict(self.person,sub=''),dict(self.person,email='invalid')):
            with self.assertRaises(GoogleError): authenticate(self.store,person)
        token,_=authenticate(self.store,self.person)
        with self.store.connection(True) as c: c.execute('UPDATE users SET active=0')
        with self.assertRaises(GoogleError): authenticate(self.store,self.person)
        with self.assertRaises(Problem): self.store.session(token)

    def test_subject_cannot_move_to_another_user(self):
        authenticate(self.store,self.person)
        other=dict(self.person,email='other@example.com')
        with self.assertRaises(GoogleError): authenticate(self.store,other)
        with self.store.connection() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)

    def test_state_browser_binding_single_use_and_expiry(self):
        state,browser,verifier=begin(self.store)
        for cookie in ('','wrong-browser'):
            with self.assertRaises(GoogleError): consume(self.store,state,cookie)
        self.assertEqual(consume(self.store,state,browser)['verifier'],verifier)
        with self.assertRaises(GoogleError): consume(self.store,state,browser)
        state,browser,_=begin(self.store)
        with self.store.connection(True) as c:c.execute('UPDATE google_auth_states SET expires=?',(time.time()-1,))
        with self.assertRaises(GoogleError): consume(self.store,state,browser)

    def test_config_disabled_until_complete_and_fixed_pkce_redirect(self):
        self.assertFalse(GoogleOAuth({}).configured)
        env={'VALHALLA_GOOGLE_CLIENT_ID':'123.apps.googleusercontent.com','VALHALLA_GOOGLE_CLIENT_SECRET':'private-test','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'}
        gateway=GoogleOAuth(env)
        self.assertTrue(gateway.configured)
        url=gateway.authorization_url('state','verifier')
        query=parse_qs(urlsplit(url).query)
        self.assertEqual(query['redirect_uri'],['https://valhallaoffice.cl/api/google/callback'])
        self.assertEqual(query['code_challenge_method'],['S256'])
        self.assertEqual(query['scope'],['openid email profile'])
        self.assertNotIn('private-test',url)
        for origin in ('http://valhallaoffice.cl','https://user:password@valhallaoffice.cl','https://valhallaoffice.cl/path'):
            self.assertFalse(GoogleOAuth(dict(env,VALHALLA_PUBLIC_URL=origin)).configured)

    def test_provider_failures_do_not_expose_secret_or_code(self):
        with patch('google_auth.build_opener',side_effect=RuntimeError('secret-and-provider-body')):
            with self.assertRaises(GoogleError) as error: GoogleOAuth({}).exchange('private-code','private-verifier')
        self.assertNotIn('secret',str(error.exception))
        self.assertNotIn('private',str(error.exception))

    def test_google_exchange_uses_fixed_https_userinfo_not_id_token(self):
        import json
        from unittest.mock import MagicMock
        from urllib.parse import parse_qs
        env={'VALHALLA_GOOGLE_CLIENT_ID':'123.apps.googleusercontent.com','VALHALLA_GOOGLE_CLIENT_SECRET':'private-test','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'}
        gateway=GoogleOAuth(env)
        token_response=MagicMock();token_response.__enter__.return_value.read.return_value=json.dumps({'access_token':'private-access','id_token':'untrusted-id-token'}).encode()
        user_response=MagicMock();user_response.__enter__.return_value.read.return_value=json.dumps(self.person).encode()
        opener=MagicMock();opener.open.side_effect=[token_response,user_response]
        with patch('google_auth.build_opener',return_value=opener):
            result=gateway.exchange('private-code','private-verifier')
        self.assertEqual(result['sub'],self.person['sub'])
        token_request=opener.open.call_args_list[0].args[0]
        user_request=opener.open.call_args_list[1].args[0]
        self.assertEqual(token_request.full_url,'https://oauth2.googleapis.com/token')
        self.assertEqual(parse_qs(token_request.data.decode())['code_verifier'],['private-verifier'])
        self.assertEqual(user_request.full_url,'https://openidconnect.googleapis.com/v1/userinfo')
        self.assertEqual(user_request.get_header('Authorization'),'Bearer private-access')

    def test_legacy_google_signup_attaches_balance_once_and_keeps_validated_name(self):
        self.store.configure_access('AdminPasswordForTests123')
        admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'AdminPasswordForTests123'}))
        self.store.import_legacy(admin['id'],{'name':'Nombre validado','email':self.person['email'],'specialty':'Psicología','reference':'old-google-123','sessions':10,'note':'Validado anteriormente','identity_confirmed':True})
        token,_=authenticate(self.store,self.person)
        user=self.store.session(token)
        self.assertEqual(user['name'],'Nombre validado')
        self.assertEqual(self.store.state(user)['packages'][0]['remaining'],10)
        with self.store.connection() as c:self.assertTrue(self.store.identity_verified(c,user['id']))
        authenticate(self.store,self.person)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM packages').fetchone()[0],1)


class GoogleHTTPTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from app import Handler
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'http.sqlite3')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=self.store
        self.server.google=GoogleOAuth({'VALHALLA_GOOGLE_CLIENT_ID':'123.apps.googleusercontent.com','VALHALLA_GOOGLE_CLIENT_SECRET':'private-test-secret','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,headers=None):
        import http.client,json
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        request_headers={'Host':'valhallaoffice.cl','Content-Type':'application/json',**(headers or {})}
        connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers=request_headers)
        response=connection.getresponse();raw=response.read();result=(response.status,response.getheaders(),raw);connection.close();return result
    def test_start_cookie_pkce_callback_single_use_no_secrets(self):
        import json
        status,headers,raw=self.request('/api/google/start',{})
        self.assertEqual(status,200)
        cookie=dict(headers)['Set-Cookie'];self.assertIn('SameSite=Lax',cookie);self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie)
        query=parse_qs(urlsplit(json.loads(raw)['authorization_url']).query)
        callback='/api/google/callback?state='+query['state'][0]+'&code=private-code'
        with patch.object(self.server.google,'exchange',return_value={'sub':'123','email':'new@example.com','name':'New Professional','email_verified':True}) as exchange:
            status,headers,raw=self.request(callback,headers={'Cookie':cookie.split(';')[0]})
            self.assertEqual(status,303);self.assertEqual(dict(headers)['Location'],'/?google_result=profile_required')
            session_cookies=[v for k,v in headers if k=='Set-Cookie' and v.startswith('session=')]
            self.assertEqual(len(session_cookies),1)
            self.assertIn('SameSite=Strict',session_cookies[0])
            self.assertNotIn('private-code',str(headers))
            status,headers,_=self.request(callback,headers={'Cookie':cookie.split(';')[0]})
            self.assertEqual(dict(headers)['Location'],'/?google_result=error');self.assertEqual(exchange.call_count,1)
    def test_origin_and_link_csrf_guards_and_disabled_provider(self):
        self.assertEqual(self.request('/api/google/start',{}, {'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/google/link',{})[0],401)
        uid=self.store.register({'email':'existing@example.com','name':'Existing Professional','specialty':'Psicología','password':'PasswordForTests123'})
        token=self.store.login({'email':'existing@example.com','password':'PasswordForTests123'})
        self.assertEqual(self.request('/api/google/link',{}, {'Cookie':'session='+token})[0],403)
        csrf=self.store.session(token)['csrf']
        self.assertEqual(self.request('/api/google/link',{}, {'Cookie':'session='+token,'X-CSRF-Token':csrf})[0],200)
        self.server.google=GoogleOAuth({})
        self.assertEqual(self.request('/api/google/start',{})[0],503)
        self.assertEqual(self.request('/api/google/config')[2],b'{"enabled": false}')


if __name__=='__main__':unittest.main()
