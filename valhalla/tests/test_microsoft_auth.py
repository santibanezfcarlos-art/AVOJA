"""Microsoft login tests use signed synthetic tokens and local databases, no provider traffic."""
import base64
import hashlib
import io
import json
import os
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from urllib.parse import parse_qs,urlsplit
from http.server import ThreadingHTTPServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa,padding
from app import Store,Handler,ADMIN_EMAIL,Problem
import google_auth
import microsoft_auth as ms

CLIENT='11111111-2222-4333-8444-555555555555'
ENV={'VALHALLA_MICROSOFT_CLIENT_ID':CLIENT,'VALHALLA_MICROSOFT_CLIENT_SECRET':'synthetic-microsoft-secret','VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl'}
PERSON={'sub':'microsoft-person-subject','email':'professional@hotmail.com','email_verified':True,'name':'Profesional Microsoft'}


def b64(raw):return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class MicrosoftIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        numbers=cls.key.public_key().public_numbers()
        cls.jwks={'keys':[{'kid':'test-key','kty':'RSA','use':'sig','alg':'RS256','n':b64(numbers.n.to_bytes(256,'big')),'e':b64(numbers.e.to_bytes(3,'big'))}]}
    def token(self,changes=None,header=None):
        claims={'iss':ms.ISSUER,'tid':ms.PERSONAL_TENANT,'aud':CLIENT,'nonce':ms.nonce_for('test-verifier'),'iat':int(time.time()),'exp':int(time.time())+600,'sub':PERSON['sub'],'email':PERSON['email'],'name':PERSON['name']}
        claims.update(changes or {})
        parts=[b64(json.dumps(header or {'alg':'RS256','kid':'test-key'}).encode()),b64(json.dumps(claims).encode())]
        signature=self.key.sign('.'.join(parts).encode(),padding.PKCS1v15(),hashes.SHA256())
        return '.'.join(parts+[b64(signature)])
    def test_signed_token_requires_microsoft_consumer_issuer_audience_nonce_and_time(self):
        result=ms.validate_id_token(self.token(),self.jwks,CLIENT,ms.nonce_for('test-verifier'))
        self.assertEqual(result['sub'],PERSON['sub'])
        for changes in ({'iss':'https://evil.example'},{'tid':'other-tenant'},{'aud':'other-client'},{'nonce':'wrong'},{'exp':time.time()-120},{'iat':time.time()+120},{'nbf':time.time()+120},{'exp':True},{'exp':float('nan')}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):ms.validate_id_token(self.token(changes),self.jwks,CLIENT,ms.nonce_for('test-verifier'))
        for header in ({'alg':'none','kid':'test-key'},{'alg':'HS256','kid':'test-key'},{'alg':'RS256','kid':'unknown'}):
            with self.assertRaises(ValueError):ms.validate_id_token(self.token(header=header),self.jwks,CLIENT,ms.nonce_for('test-verifier'))
        token=self.token();parts=token.split('.');parts[1]=b64(json.dumps({'aud':CLIENT}).encode())
        with self.assertRaises(Exception):ms.validate_id_token('.'.join(parts),self.jwks,CLIENT,ms.nonce_for('test-verifier'))
    def test_config_requires_credentials_and_canonical_https_origin(self):
        self.assertFalse(ms.MicrosoftOAuth({}).configured)
        gateway=ms.MicrosoftOAuth(ENV);self.assertTrue(gateway.configured)
        url=gateway.authorization_url('test-state','test-verifier');parts=urlsplit(url);query=parse_qs(parts.query)
        self.assertEqual(parts.netloc,'login.microsoftonline.com');self.assertEqual(parts.path,'/consumers/oauth2/v2.0/authorize')
        self.assertEqual(query['redirect_uri'],['https://valhallaoffice.cl/api/microsoft/callback'])
        self.assertEqual(query['scope'],['openid profile email']);self.assertEqual(query['nonce'],[ms.nonce_for('test-verifier')])
        self.assertEqual(query['code_challenge'],[b64(hashlib.sha256(b'test-verifier').digest())]);self.assertNotIn(ENV['VALHALLA_MICROSOFT_CLIENT_SECRET'],url)
        for change in ({'VALHALLA_PUBLIC_URL':'http://valhallaoffice.cl'},{'VALHALLA_PUBLIC_URL':'https://user:pass@valhallaoffice.cl'},{'VALHALLA_MICROSOFT_CLIENT_ID':'bad'},{'VALHALLA_PUBLIC_URL':'https://valhallaoffice.cl/path'}):
            self.assertFalse(ms.MicrosoftOAuth(ENV|change).configured)
    def test_exchange_uses_only_fixed_endpoints_and_checks_userinfo_subject(self):
        def response(body):
            r=MagicMock();r.__enter__.return_value.read.return_value=json.dumps(body).encode();return r
        opener=MagicMock();opener.open.side_effect=[response({'id_token':self.token(),'access_token':'synthetic-private-access'}),response(self.jwks),response(PERSON)]
        with patch.object(ms,'build_opener',return_value=opener):person=ms.MicrosoftOAuth(ENV).exchange('private-auth-code','test-verifier')
        self.assertEqual(person['email'],PERSON['email']);self.assertTrue(person['email_verified'])
        requests=[call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([r.full_url for r in requests],[ms.AUTHORITY+'/oauth2/v2.0/token',ms.AUTHORITY+'/discovery/v2.0/keys','https://graph.microsoft.com/oidc/userinfo'])
        self.assertEqual(parse_qs(requests[0].data.decode())['code_verifier'],['test-verifier'])
        self.assertEqual(requests[2].get_header('Authorization'),'Bearer synthetic-private-access')
        opener.open.side_effect=[response({'id_token':self.token(),'access_token':'synthetic-private-access'}),response(self.jwks),response(PERSON|{'sub':'different-person'})]
        with patch.object(ms,'build_opener',return_value=opener),self.assertRaises(ms.MicrosoftError):ms.MicrosoftOAuth(ENV).exchange('private-auth-code','test-verifier')
        opener.open.side_effect=[response({'id_token':self.token({'email':'someone@external.example'}),'access_token':'synthetic-private-access'}),response(self.jwks),response(PERSON|{'email':'someone@external.example'})]
        with patch.object(ms,'build_opener',return_value=opener),self.assertRaises(ms.MicrosoftError) as error:ms.MicrosoftOAuth(ENV).exchange('private-auth-code','test-verifier')
        self.assertEqual(error.exception.result,'unsupported_email')
    def test_provider_error_never_exposes_private_fields(self):
        with patch.object(ms,'build_opener',side_effect=RuntimeError('synthetic-private-secret-and-provider-body')):
            with self.assertRaises(ms.MicrosoftError) as error:ms.MicrosoftOAuth(ENV).exchange('private-auth-code','private-verifier')
        self.assertNotIn('private',str(error.exception))


class MicrosoftAccounts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'microsoft.sqlite3');self.store.configure_access('SyntheticAdmin123!')
        self.admin_token=self.store.login({'email':ADMIN_EMAIL,'password':'SyntheticAdmin123!'})
        self.admin=self.store.session(self.admin_token)['id']
    def register(self):return self.store.register({'name':'Nombre original','email':PERSON['email'],'specialty':'Psicología','password':'SyntheticProfessional123!'})
    def test_new_microsoft_signup_is_professional_and_requires_documents_and_specialty(self):
        token,result=ms.authenticate(self.store,PERSON);user=self.store.session(token)
        self.assertEqual(result,'profile_required');self.assertEqual(user['role'],'professional');self.assertTrue(user['email_verified'])
        state=self.store.state(user);self.assertTrue(state['user']['microsoft_linked']);self.assertFalse(state['user']['google_linked'])
        self.assertTrue(state['user']['profile_required']);self.assertFalse(state['user']['verified'])
        token2,_=ms.authenticate(self.store,PERSON);self.assertEqual(self.store.session(token2)['id'],user['id'])
        with self.store.connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_outbox WHERE kind='admin_signup'").fetchone()[0],1)
    def test_existing_password_account_requires_explicit_link_and_preserves_balance(self):
        uid=self.register()
        with self.assertRaises(ms.MicrosoftError) as error:ms.authenticate(self.store,PERSON)
        self.assertEqual(error.exception.result,'link_required')
        session=self.store.login({'email':PERSON['email'],'password':'SyntheticProfessional123!'})
        self.store.import_legacy(self.admin,{'name':'Nombre original','email':PERSON['email'],'specialty':'Psicología','reference':'ms-existing','sessions':8,'identity_confirmed':True})
        token,result=ms.authenticate(self.store,PERSON,session);self.assertEqual(result,'success')
        state=self.store.state(self.store.session(token));self.assertEqual(state['user']['id'],uid);self.assertEqual(state['user']['name'],'Nombre original');self.assertEqual(state['packages'][0]['remaining'],8)
        self.assertEqual(self.store.session(ms.authenticate(self.store,PERSON)[0])['id'],uid)
        with self.assertRaises(ms.MicrosoftError):ms.authenticate(self.store,PERSON|{'sub':'other-subject'},session)
    def test_migrated_hotmail_client_receives_hours_once_and_completes_specialty(self):
        self.store.import_legacy(self.admin,{'name':'Persona validada','email':PERSON['email'],'specialty':'','reference':'ms-migrated','sessions':200,'identity_confirmed':True})
        token,_=ms.authenticate(self.store,PERSON);state=self.store.state(self.store.session(token))
        self.assertEqual(state['user']['name'],'Persona validada');self.assertEqual(state['user']['identity_source'],'legacy')
        self.assertTrue(state['user']['profile_required']);self.assertEqual(state['packages'][0]['remaining'],200)
        ms.authenticate(self.store,PERSON)
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM packages').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notification_outbox WHERE kind='hours_credited'").fetchone()[0],0)
    def test_states_are_provider_isolated_browser_bound_expiring_and_single_use(self):
        state,browser,verifier=ms.begin(self.store)
        with self.assertRaises(ms.MicrosoftError):ms.consume(self.store,state,'other-browser')
        with self.assertRaises(google_auth.GoogleError):google_auth.consume(self.store,state,browser)
        self.assertEqual(ms.consume(self.store,state,browser)['verifier'],verifier)
        with self.assertRaises(ms.MicrosoftError):ms.consume(self.store,state,browser)
        state,browser,_=ms.begin(self.store)
        with self.store.connection(True) as c:c.execute('UPDATE microsoft_auth_states SET expires=0')
        with self.assertRaises(ms.MicrosoftError):ms.consume(self.store,state,browser)
        with self.assertRaises(google_auth.GoogleError):google_auth.begin(self.store,provider='invalid;DROP TABLE users')
    def test_blocked_inactive_deleted_and_admin_cannot_gain_access(self):
        for person in (PERSON|{'email':ADMIN_EMAIL},PERSON|{'email_verified':False}):
            with self.assertRaises(ms.MicrosoftError):ms.authenticate(self.store,person)
        token,_=ms.authenticate(self.store,PERSON);uid=self.store.session(token)['id']
        self.store.manage_user_access(self.admin,{'user_id':uid,'action':'block','confirmed':True})
        with self.assertRaises(ms.MicrosoftError):ms.authenticate(self.store,PERSON)
        self.store.manage_user_access(self.admin,{'email':PERSON['email'],'action':'unblock','confirmed':True})
        with self.store.connection(True) as c:c.execute('UPDATE users SET active=0 WHERE id=?',(uid,))
        with self.assertRaises(ms.MicrosoftError):ms.authenticate(self.store,PERSON)
    def test_deleted_account_unlinks_microsoft_subject(self):
        token,_=ms.authenticate(self.store,PERSON);uid=self.store.session(token)['id']
        self.store.manage_user_access(self.admin,{'user_id':uid,'action':'delete','confirmed':True})
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM microsoft_accounts').fetchone()[0],0)


class MicrosoftHTTP(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'http.sqlite3')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.store=self.store;self.server.microsoft=ms.MicrosoftOAuth(ENV)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def request(self,path,body=None,headers=None):
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        connection.request('POST' if body is not None else 'GET',path,body=json.dumps(body) if body is not None else None,headers={'Host':'valhallaoffice.cl','Content-Type':'application/json',**(headers or {})})
        response=connection.getresponse();raw=response.read();result=(response.status,response.getheaders(),raw);connection.close();return result
    def test_callback_sets_session_clears_flow_cookie_and_does_not_replay(self):
        status,headers,raw=self.request('/api/microsoft/start',{});self.assertEqual(status,200)
        cookie=dict(headers)['Set-Cookie'];self.assertIn('SameSite=Lax',cookie);self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie)
        query=parse_qs(urlsplit(json.loads(raw)['authorization_url']).query)
        callback='/api/microsoft/callback?state='+query['state'][0]+'&code=private-auth-code'
        with patch.object(self.server.microsoft,'exchange',return_value=PERSON) as exchange:
            status,headers,_=self.request(callback,headers={'Cookie':cookie.split(';')[0]})
            self.assertEqual(status,303);self.assertEqual(dict(headers)['Location'],'/?microsoft_result=profile_required')
            sessions=[v for k,v in headers if k=='Set-Cookie' and v.startswith('session=')];self.assertEqual(len(sessions),1);self.assertIn('SameSite=Strict',sessions[0])
            self.assertNotIn('private-auth-code',str(headers))
            status,headers,_=self.request(callback,headers={'Cookie':cookie.split(';')[0]})
            self.assertEqual(dict(headers)['Location'],'/?microsoft_result=error');self.assertEqual(exchange.call_count,1)
    def test_cancel_wrong_host_csrf_and_disabled_provider(self):
        self.assertEqual(self.request('/api/microsoft/start',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/microsoft/start',{},headers={'Host':'evil.example'})[0],400)
        self.assertEqual(self.request('/api/microsoft/link',{})[0],401)
        uid=self.store.register({'email':PERSON['email'],'name':'Persona','specialty':'Psicología','password':'SyntheticProfessional123!'})
        token=self.store.login({'email':PERSON['email'],'password':'SyntheticProfessional123!'})
        self.assertEqual(self.request('/api/microsoft/link',{},headers={'Cookie':'session='+token})[0],403)
        self.assertEqual(self.request('/api/microsoft/link',{},headers={'Cookie':'session='+token,'X-CSRF-Token':self.store.session(token)['csrf']})[0],200)
        _,headers,raw=self.request('/api/microsoft/start',{});cookie=dict(headers)['Set-Cookie'];state=parse_qs(urlsplit(json.loads(raw)['authorization_url']).query)['state'][0]
        _,headers,_=self.request('/api/microsoft/callback?state='+state+'&error=access_denied',headers={'Cookie':cookie.split(';')[0]})
        self.assertEqual(dict(headers)['Location'],'/?microsoft_result=cancelled')
        self.server.microsoft=ms.MicrosoftOAuth({})
        self.assertEqual(self.request('/api/microsoft/start',{})[0],503)
        self.assertEqual(json.loads(self.request('/api/microsoft/config')[2]),{'enabled':False})
