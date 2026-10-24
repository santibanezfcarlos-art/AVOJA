"""One-owner OAuth bootstrap for Google Calendar. Disabled unless configured.

No OAuth secrets, refresh tokens or access tokens are returned to browsers.
This only establishes authorization; calendar writer migration is separate.
"""
import os
import secrets
import hashlib
import hmac
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from flask import Blueprint, request, redirect, make_response
from cryptography.fernet import Fernet, InvalidToken
from booking_holds import _database_connection

oauth_bp = Blueprint("psc_google_oauth", __name__)
REDIRECT = "https://psicologia-retenciones-piloto.onrender.com/oauth/google/callback"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
COOKIE = "psc_google_auth_state"

def configured():
    return (os.environ.get("PSICOLOGIA_GOOGLE_OAUTH_ENABLED") == "true"
            and len(os.environ.get("PSICOLOGIA_GOOGLE_OAUTH_CLIENT_ID","")) > 20
            and len(os.environ.get("PSICOLOGIA_GOOGLE_OAUTH_CLIENT_SECRET","")) > 20
            and len(os.environ.get("PSICOLOGIA_GOOGLE_OAUTH_SETUP_KEY","")) >= 32
            and bool(os.environ.get("PSICOLOGIA_GOOGLE_OAUTH_FERNET_KEY")))

def message(txt, status=200):
    response=make_response(txt,status)
    response.headers["Cache-Control"]="no-store"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Robots-Tag"]="noindex"
    return response

@oauth_bp.get("/oauth/google/start")
def start():
    if not configured():
        return message("Autorización aún no configurada en Render.",503)
    supplied=request.args.get("setup_key","")
    key=os.environ["PSICOLOGIA_GOOGLE_OAUTH_SETUP_KEY"]
    if not hmac.compare_digest(supplied,key):
        return message("Acceso no autorizado.",403)
    try:
        Fernet(os.environ["PSICOLOGIA_GOOGLE_OAUTH_FERNET_KEY"].encode())
    except (ValueError,TypeError):
        return message("Clave de cifrado no válida.",503)
    state=secrets.token_urlsafe(32)
    params=dict(client_id=os.environ["PSICOLOGIA_GOOGLE_OAUTH_CLIENT_ID"],
                redirect_uri=REDIRECT,response_type="code",scope=SCOPE,
                access_type="offline",prompt="consent",state=state)
    response=redirect(AUTH+"?"+urlencode(params))
    response.set_cookie(COOKIE,hashlib.sha256(state.encode()).hexdigest(),
                        max_age=600,secure=True,httponly=True,samesite="Lax",path="/oauth/google")
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Cache-Control"]="no-store"
    return response

@oauth_bp.get("/oauth/google/callback")
def callback():
    if not configured():
        return message("Autorización deshabilitada.",503)
    state=request.args.get("state","")
    cookie=request.cookies.get(COOKIE,"")
    if not (state and cookie and hmac.compare_digest(
            hashlib.sha256(state.encode()).hexdigest(),cookie)):
        return message("Autorización inválida o vencida. Repite el inicio.",403)
    if request.args.get("error"):
        return message("Google no autorizó el acceso.",400)
    code=request.args.get("code","")
    if not code or len(code)>2048:
        return message("Código de autorización inválido.",400)
    data=urlencode(dict(code=code,client_id=os.environ["PSICOLOGIA_GOOGLE_OAUTH_CLIENT_ID"],
       client_secret=os.environ["PSICOLOGIA_GOOGLE_OAUTH_CLIENT_SECRET"],
       redirect_uri=REDIRECT,grant_type="authorization_code")).encode()
    try:
        req=Request(TOKEN,data=data,headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urlopen(req,timeout=15) as result:
            credentials=json.loads(result.read(10000))
        refresh=credentials.get("refresh_token")
        scopes=set(credentials.get("scope","").split())
        if not refresh or SCOPE not in scopes:
            return message("Google no entregó autorización permanente para Calendar.",409)
        encrypted=Fernet(os.environ["PSICOLOGIA_GOOGLE_OAUTH_FERNET_KEY"].encode()).encrypt(
                  refresh.encode()).decode()
        with _database_connection() as db:
            db.execute("""INSERT INTO psychology_google_oauth (key,encrypted_refresh_token,updated_at)
                          VALUES ('owner',%s,now()) ON CONFLICT(key) DO UPDATE
                          SET encrypted_refresh_token=excluded.encrypted_refresh_token,
                              updated_at=now()""",(encrypted,))
    except (HTTPError,URLError,TimeoutError,ValueError,KeyError,InvalidToken):
        return message("No se pudo completar la autorización con Google.",503)
    except Exception:
        return message("No se pudo guardar la autorización. Comprueba PostgreSQL.",503)
    response=message("Autorización de Google Calendar guardada. Ya puedes cerrar esta ventana.")
    response.delete_cookie(COOKIE,path="/oauth/google")
    return response
