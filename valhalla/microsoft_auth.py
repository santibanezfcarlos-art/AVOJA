"""Microsoft personal-account authorization code flow with PKCE and validated OIDC tokens."""
import base64
import hashlib
import hmac
import json
import math
import os
import re
import time
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa,padding
import google_auth

PERSONAL_TENANT='9188040d-6c67-4c5b-b112-36a304b66dad'
ISSUER='https://login.microsoftonline.com/'+PERSONAL_TENANT+'/v2.0'
AUTHORITY='https://login.microsoftonline.com/consumers'
# These addresses are issued by Microsoft; arbitrary external email claims are not proof of ownership.
MICROSOFT_EMAIL_DOMAINS={'hotmail.com','outlook.com','live.com','msn.com','hotmail.es','outlook.es','live.cl'}


class MicrosoftError(Exception):
    def __init__(self,result='error'):
        self.result=result
        super().__init__('No se pudo completar el acceso con Microsoft.')


# Shared state/account logic preserves the existing account and legacy balance rules.
def begin(store,session_token=''):
    try:return google_auth.begin(store,session_token,provider='microsoft')
    except google_auth.GoogleError as error:raise MicrosoftError(error.result) from None


def consume(store,state,browser):
    try:return google_auth.consume(store,state,browser,provider='microsoft')
    except google_auth.GoogleError as error:raise MicrosoftError(error.result) from None


def authenticate(store,identity,link_session=''):
    email=str(identity.get('email','')).strip().lower()
    if email.rsplit('@',1)[-1] not in MICROSOFT_EMAIL_DOMAINS:raise MicrosoftError('unsupported_email')
    try:return google_auth.authenticate(store,identity,link_session,provider='microsoft')
    except google_auth.GoogleError as error:raise MicrosoftError(error.result) from None


def nonce_for(verifier):
    return hashlib.sha256(('microsoft-nonce:'+verifier).encode()).hexdigest()


def decode64(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value):raise ValueError()
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def validate_id_token(token,jwks,client_id,nonce):
    if not isinstance(token,str) or len(token)>32768:raise ValueError()
    header_part,payload_part,signature_part=token.split('.')
    header=json.loads(decode64(header_part));claims=json.loads(decode64(payload_part))
    if header.get('alg')!='RS256' or not isinstance(header.get('kid'),str) or header.get('crit'):raise ValueError()
    keys=[key for key in jwks.get('keys',[]) if key.get('kid')==header['kid'] and key.get('kty')=='RSA' and key.get('use','sig')=='sig' and key.get('alg','RS256')=='RS256']
    if len(keys)!=1:raise ValueError()
    key=keys[0];n=int.from_bytes(decode64(key['n']),'big');e=int.from_bytes(decode64(key['e']),'big')
    if not 2048<=n.bit_length()<=4096 or not 3<=e<=65537 or e%2!=1:raise ValueError()
    rsa.RSAPublicNumbers(e,n).public_key().verify(decode64(signature_part),(header_part+'.'+payload_part).encode('ascii'),padding.PKCS1v15(),hashes.SHA256())
    current=time.time()
    if claims.get('iss')!=ISSUER or claims.get('tid')!=PERSONAL_TENANT or claims.get('aud')!=client_id:raise ValueError()
    if not isinstance(claims.get('nonce'),str) or not hmac.compare_digest(claims['nonce'],nonce):raise ValueError()
    for field in ('exp','iat','nbf'):
        value=claims.get(field)
        if field=='nbf' and value is None:continue
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError()
        if field=='exp' and value<=current-60:raise ValueError()
        if field in ('iat','nbf') and value>current+60:raise ValueError()
    subject=claims.get('sub')
    if not isinstance(subject,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}',subject):raise ValueError()
    return claims


class MicrosoftOAuth:
    def __init__(self,env=None):
        env=os.environ if env is None else env
        self.client_id=env.get('VALHALLA_MICROSOFT_CLIENT_ID','').strip().lower()
        self.secret=env.get('VALHALLA_MICROSOFT_CLIENT_SECRET','').strip()
        self.origin=env.get('VALHALLA_PUBLIC_URL','').rstrip('/')
        try:parsed=urlsplit(self.origin)
        except ValueError:parsed=urlsplit('')
        self.configured=bool(re.fullmatch(r'[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}',self.client_id) and self.secret and parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
        self.redirect_uri=self.origin+'/api/microsoft/callback'

    def authorization_url(self,state,verifier):
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return AUTHORITY+'/oauth2/v2.0/authorize?'+urlencode(dict(client_id=self.client_id,redirect_uri=self.redirect_uri,response_type='code',response_mode='query',scope='openid profile email',state=state,nonce=nonce_for(verifier),code_challenge=challenge,code_challenge_method='S256',prompt='select_account'))

    def exchange(self,code,verifier):
        try:
            opener=build_opener(google_auth.NoRedirect())
            def read(request):
                with opener.open(request,timeout=15) as response:
                    raw=response.read(262145)
                    if len(raw)>262144:raise ValueError()
                    return json.loads(raw)
            tokens=read(Request(AUTHORITY+'/oauth2/v2.0/token',data=urlencode(dict(client_id=self.client_id,client_secret=self.secret,code=code,code_verifier=verifier,redirect_uri=self.redirect_uri,grant_type='authorization_code',scope='openid profile email')).encode(),headers={'Content-Type':'application/x-www-form-urlencoded','User-Agent':'ValhallaOffice/1.0'}))
            keys=read(Request(AUTHORITY+'/discovery/v2.0/keys',headers={'User-Agent':'ValhallaOffice/1.0'}))
            claims=validate_id_token(tokens['id_token'],keys,self.client_id,nonce_for(verifier))
            access=tokens['access_token']
            if not isinstance(access,str) or not 1<=len(access)<=16384 or '\r' in access or '\n' in access:raise ValueError()
            person=read(Request('https://graph.microsoft.com/oidc/userinfo',headers={'Authorization':'Bearer '+access,'User-Agent':'ValhallaOffice/1.0'}))
            if person.get('sub')!=claims['sub']:raise ValueError()
            email=str(person.get('email') or claims.get('email') or '').strip().lower()
            if email.rsplit('@',1)[-1] not in MICROSOFT_EMAIL_DOMAINS:raise MicrosoftError('unsupported_email')
            if claims.get('email') and str(claims['email']).strip().lower()!=email:raise ValueError()
            return google_auth.validate_identity({'sub':claims['sub'],'email':email,'email_verified':True,'name':person.get('name') or claims.get('name') or 'Profesional'})
        except MicrosoftError:raise
        except Exception:raise MicrosoftError() from None
