"""Server-side Google code flow. No Gmail access and no provider tokens stored."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class GoogleError(Exception):
    def __init__(self, result='error'):
        self.result = result
        super().__init__('No se pudo completar el acceso con Google.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GoogleOAuth:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.client_id = env.get('VALHALLA_GOOGLE_CLIENT_ID', '').strip()
        self.secret = env.get('VALHALLA_GOOGLE_CLIENT_SECRET', '').strip()
        self.origin = env.get('VALHALLA_PUBLIC_URL', '').rstrip('/')
        try:
            parsed = urlsplit(self.origin)
        except ValueError:
            parsed = urlsplit('')
        self.configured = bool(re.fullmatch(r'[\w.-]+\.apps\.googleusercontent\.com', self.client_id) and self.secret and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment)
        self.redirect_uri = self.origin + '/api/google/callback'

    def authorization_url(self, state, verifier):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(dict(client_id=self.client_id, redirect_uri=self.redirect_uri, response_type='code', scope='openid email profile', state=state, code_challenge=challenge, code_challenge_method='S256', prompt='select_account'))

    def exchange(self, code, verifier):
        try:
            opener = build_opener(NoRedirect())
            def read(request):
                with opener.open(request, timeout=15) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ValueError()
                    return json.loads(raw)
            token = read(Request('https://oauth2.googleapis.com/token', data=urlencode(dict(client_id=self.client_id, client_secret=self.secret, code=code, code_verifier=verifier, redirect_uri=self.redirect_uri, grant_type='authorization_code')).encode(), headers={'Content-Type':'application/x-www-form-urlencoded', 'User-Agent':'ValhallaOffice/1.0'}))
            access = token['access_token']
            if not isinstance(access, str) or not 1 <= len(access) <= 8192 or '\r' in access or '\n' in access:
                raise ValueError()
            # Trust only Google's HTTPS userinfo response, never an unverified ID token.
            return validate_identity(read(Request('https://openidconnect.googleapis.com/v1/userinfo', headers={'Authorization':'Bearer ' + access, 'User-Agent':'ValhallaOffice/1.0'})))
        except Exception:
            raise GoogleError() from None


def validate_identity(identity):
    email = str(identity.get('email', '')).strip().lower()
    subject = identity.get('sub')
    if identity.get('email_verified') is not True or not isinstance(subject, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}', subject) or len(email)>200 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise GoogleError()
    name = str(identity.get('name', '')).strip()[:100]
    return {'sub':subject, 'email':email, 'email_verified':True, 'name': name if len(name)>=2 else 'Profesional'}


def begin(store, session_token=''):
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    with store.connection(True) as c:
        c.execute('DELETE FROM google_auth_states WHERE expires<=?', (time.time(),))
        if session_token:
            user = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?", (session_token,time.time())).fetchone()
            if not user or not user['active'] or user['role']!='professional':
                raise GoogleError()
        c.execute('INSERT INTO google_auth_states VALUES(?,?,?,?,?)', (digest(state), digest(browser), verifier, time.time()+600, session_token))
    return state, browser, verifier


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def consume(store, state, browser):
    if not state or not browser or len(state)>200 or len(browser)>200:
        raise GoogleError()
    with store.connection(True) as c:
        row = c.execute('SELECT * FROM google_auth_states WHERE state_hash=?', (digest(state),)).fetchone()
        if not row or row['expires']<=time.time() or not hmac.compare_digest(row['browser_hash'],digest(browser)):
            raise GoogleError()
        c.execute('DELETE FROM google_auth_states WHERE state_hash=?',(digest(state),))
        return dict(row)


def authenticate(store, identity, link_session=''):
    from app import ADMIN_EMAIL, LEGACY_DEMO_EMAILS, password_hash
    person = validate_identity(identity)
    email, subject = person['email'], person['sub']
    if email == ADMIN_EMAIL or email in LEGACY_DEMO_EMAILS:
        raise GoogleError('admin_password')
    with store.connection(True) as c:
        linked = c.execute('SELECT * FROM google_accounts WHERE subject=?',(subject,)).fetchone()
        existing = c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
        if link_session:
            session = c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(link_session,time.time())).fetchone()
            if not session or not existing or session['user_id']!=existing['id']:
                raise GoogleError()
            if linked and linked['user_id']!=existing['id']:
                raise GoogleError()
            other = c.execute('SELECT subject FROM google_accounts WHERE user_id=?',(existing['id'],)).fetchone()
            if other and other['subject']!=subject:
                raise GoogleError()
        elif linked:
            if not existing or linked['user_id']!=existing['id']:
                raise GoogleError()
        elif existing:
            # A matching email alone is insufficient to link an existing account.
            raise GoogleError('link_required')
        else:
            legacy = c.execute('SELECT name,specialty FROM legacy_imports WHERE email=? AND user_id IS NULL',(email,)).fetchone()
            name = legacy['name'] if legacy else person['name']
            specialty = legacy['specialty'] if legacy else ''
            uid = c.execute('INSERT INTO users(name,email,password,specialty,email_verified) VALUES(?,?,?,?,1)',(name,email,password_hash(secrets.token_urlsafe(48)),specialty)).lastrowid
            store.attach_legacy(c,uid,email)
            store.log(c,uid,'registered',uid)
            store.queue_notification(c,uid,'admin_signup',uid,ADMIN_EMAIL,{'name':name,'email':email,'specialty':specialty})
            existing = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not existing['active'] or existing['role']!='professional':
            raise GoogleError()
        uid = existing['id']
        c.execute('INSERT OR IGNORE INTO google_accounts VALUES(?,?,?)',(subject,uid,email))
        c.execute('UPDATE users SET email_verified=1 WHERE id=?',(uid,))
        store.log(c,uid,'google_linked' if link_session else 'google_login',uid)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        c.execute('DELETE FROM sessions WHERE expires<=?',(time.time(),))
        c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(token,uid,csrf,time.time()+28800))
        return token, 'success' if existing['specialty'] else 'profile_required'
