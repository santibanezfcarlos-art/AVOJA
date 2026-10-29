"""Short-lived, visible SII Chromium session over noVNC.

Credentials are fetched server-to-server from the PSC API and used only in
memory to sign in and stop at the calculator. The browser blocks the final EMITIR action.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from flask import Flask, Response, jsonify, request, send_file
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096
_lock = threading.Lock()
_used_nonces = set()
_session = {"state": "idle", "browser": None, "started": None, "stage": "idle"}
SESSION_TTL = 15 * 60
TARGET = "https://eboleta.sii.cl/emitir/"

BLOCK_ISSUE_ACTIONS = r"""
(() => {
  const label = (el) => (el?.innerText || el?.value || el?.getAttribute("aria-label") || "")
    .replace(/\s+/g, " ").trim().toUpperCase();
  const isIssueAction = (el) => label(el).startsWith("EMITIR");
  const protect = (event) => {
    const el = event.target?.closest?.("button, input[type=submit], [role=button], a");
    if (el && isIssueAction(el)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const notice = document.getElementById("__psc_issue_guard");
      if (notice) notice.style.display = "block";
    }
  };
  document.addEventListener("click", protect, true);
  document.addEventListener("submit", (event) => {
    const submitter = event.submitter;
    const finalAction = (submitter && isIssueAction(submitter)) ||
      (!submitter && Array.from(event.target.querySelectorAll("button,input[type=submit],[role=button]")).some(isIssueAction));
    if (finalAction) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const notice = document.getElementById("__psc_issue_guard");
      if (notice) notice.style.display = "block";
    }
  }, true);
  const addWarning = () => {
    if (!document.body || document.getElementById("__psc_issue_guard")) return;
    const banner = document.createElement("div");
    banner.id = "__psc_issue_guard";
    banner.textContent = "PRUEBA VISUAL · NO EMITIR · El botón EMITIR está bloqueado en esta sesión.";
    Object.assign(banner.style, {
      position:"fixed", top:"0", left:"0", right:"0", zIndex:"2147483647",
      padding:"10px", background:"#8b1e1e", color:"#fff", font:"bold 16px sans-serif",
      textAlign:"center", boxShadow:"0 2px 8px #333"
    });
    document.body.appendChild(banner);
  };
  const observer = new MutationObserver(addWarning);
  if (document.documentElement) observer.observe(document.documentElement, {childList:true,subtree:true});
  document.addEventListener("DOMContentLoaded", addWarning, {once:true});
  addWarning();
})();
"""


def _secret(name):
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise RuntimeError("visual session secret missing")
    return value.encode()


def _decode_ticket(ticket):
    try:
        payload, signature = ticket.split(".", 1)
        expected = hmac.new(_secret("SII_VISUAL_TICKET_SECRET"), payload.encode(), hashlib.sha256).digest()
        received = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        if not hmac.compare_digest(expected, received):
            return None
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("nonce"), str):
            return None
        now = int(time.time())
        if type(data.get("exp")) is not int or not now <= data["exp"] <= now + 600:
            return None
        return data
    except Exception:
        return None


def _fetch_sii_credentials(ticket):
    """Fetch secrets server-side; never return or log them to the browser client."""
    api_url = os.environ.get("SII_CREDENTIALS_API_URL", "").rstrip("/")
    if not api_url.startswith("https://"):
        raise RuntimeError("SII credential service is not configured")
    body = json.dumps({"ticket": ticket}).encode("utf-8")
    req = urllib.request.Request(
        api_url + "/admin/sii-visual/internal-credentials",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-PSC-Visual-Bridge": os.environ.get("SII_VISUAL_TICKET_SECRET", ""),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read(4096).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("SII credentials could not be retrieved") from exc
    rut, password = data.get("rut"), data.get("password")
    if not isinstance(rut, str) or not rut or not isinstance(password, str) or not password:
        raise RuntimeError("SII credentials are not configured in the PSC API")
    return {"rut": rut, "password": password}


def _fill_login_only(page, credentials):
    """Wait through redirects, fill fields, and never submit."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if _session.get("stop", False):
            return "stopped"
        for frame in page.frames:
            if urlparse(frame.url).hostname != "clave.w.sii.cl":
                continue
            passwords = frame.locator('input[type="password"]:visible')
            ruts = frame.locator(
                'input[name*="rut" i]:visible, input[id*="rut" i]:visible, '
                'input[autocomplete="username"]:visible, input[type="text"]:visible, input[type="tel"]:visible'
            )
            if passwords.count() == 0:
                continue
            for i in range(min(ruts.count(), 12)):
                rut, password = ruts.nth(i), passwords.first
                if not rut.is_enabled() or not password.is_enabled():
                    continue
                password.evaluate("(el) => el.setAttribute('data-psc-secret', 'true')")
                rut.fill(credentials["rut"], timeout=10000)
                password.fill(credentials["password"], timeout=10000)
                normalize_rut = lambda value: value.replace(".", "").replace("-", "").replace(" ", "").upper()
                rut_matches = normalize_rut(rut.input_value()) == normalize_rut(credentials["rut"])
                password_matches = password.input_value() == credentials["password"]
                with _lock:
                    _session["field_check"] = {"rut_matches": rut_matches, "password_matches": password_matches}
                verified = rut_matches and password_matches
                if not verified:
                    return "field_verification_failed"
                with _lock:
                    _session["stage"] = "waiting_before_login"
                _update_preview(page)
                page.wait_for_timeout(5000)
                if _session.get("stop", False):
                    return "stopped"
                # Check again after the pause; never send modified or incomplete fields.
                if (normalize_rut(rut.input_value()) != normalize_rut(credentials["rut"])
                        or password.input_value() != credentials["password"]):
                    return "field_verification_failed"
                if urlparse(frame.url).hostname != "clave.w.sii.cl":
                    return "unexpected_auth_host"
                submit = frame.get_by_role("button", name=re.compile(r"^Ingresar$", re.I))
                if submit.count() != 1 or not submit.is_visible() or not submit.is_enabled():
                    return "login_submit_not_recognized"
                with _lock:
                    _session["stage"] = "waiting_calculator"
                    _session["login_click_attempted"] = True
                # One attempt only: no retry if the response is slow.
                submit.click(timeout=10000)
                with _lock:
                    _session["submitted"] = True
                deadline_login = time.monotonic() + 120
                while time.monotonic() < deadline_login:
                    if _session.get("stop", False):
                        return "stopped"
                    if _calculator_ready(page):
                        return "signed_in"
                    _update_preview(page)
                    page.wait_for_timeout(1000)
                return "login_not_completed"
        page.wait_for_timeout(500)
    return "login_form_not_loaded"


def _calculator_ready(page):
    if urlparse(page.url).hostname != "eboleta.sii.cl":
        return False
    try:
        # Match real calculator controls, never the injected EMITIR warning banner.
        controls = page.locator("button, input[type=button], [role=button]")
        return all(controls.filter(has_text=re.compile("^" + label + "$")).first.is_visible()
                   for label in ("7", "8", "0", "EMITIR"))
    except Exception:
        return False


def _update_preview(page):
    try:
        preview = page.screenshot(type="jpeg", quality=65,
                                 mask=[page.locator('input[type="password"], input[autocomplete="current-password"], input[data-psc-secret]')],
                                 timeout=5000)
        with _lock:
            _session["preview"] = preview
    except Exception:
        pass


def _close_browser():
    browser = _session.get("browser")
    _session["browser"] = None
    if browser:
        try:
            browser.close()
        except Exception:
            pass


def _expire_session(started):
    time.sleep(SESSION_TTL)
    with _lock:
        if _session.get("started") == started:
            _close_browser()
            _session.update(state="expired", started=None)


def _browser_owner(started, credentials):
    stage = "chromium_launch"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
                timeout=30000,
            )
            with _lock:
                if _session.get("started") != started:
                    browser.close()
                    return
                _session["browser"] = browser
                _session["state"] = "open"
                _session["stage"] = "opening_sii"
            context = browser.new_context(locale="es-CL", timezone_id="America/Santiago",
                                          viewport={"width":1365,"height":768})
            context.add_init_script(BLOCK_ISSUE_ACTIONS)
            page = context.new_page()
            response = None
            try:
                response = page.goto(TARGET, wait_until="commit", timeout=60000)
            except PlaywrightTimeoutError:
                app.logger.warning("SII_VISUAL_NAVIGATION_TIMEOUT stage=commit")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=60000)
            except PlaywrightTimeoutError:
                app.logger.warning("SII_VISUAL_NAVIGATION_TIMEOUT stage=domcontentloaded")
            page.wait_for_timeout(2000)
            stage = "sii_authentication"
            with _lock:
                if _session.get("started") == started:
                    _session["stage"] = stage
            try:
                auth_result = _fill_login_only(page, credentials)
            finally:
                credentials.clear()
            calculator = auth_result == "signed_in" and _calculator_ready(page)
            host = urlparse(page.url).hostname or ""
            safe_result = {
                "connection": "direct",
                "sii_http_status": response.status if response else None,
                "final_host": host if host.endswith(".sii.cl") else "other",
                "authentication": auth_result,
                "submitted": _session.get("submitted", False),
                "login_click_attempted": _session.get("login_click_attempted", False),
                "calculator_ready": calculator,
            }
            with _lock:
                if _session.get("started") == started:
                    _session["connectivity"] = safe_result
                    _session["state"] = "open"
                    _session["stage"] = "calculator_ready" if calculator else "sii_page_open"
            app.logger.warning("SII_VISUAL_DIRECT_LOGIN %s", json.dumps(safe_result, separators=(",", ":")))
            page.bring_to_front()
            while time.monotonic() - started < SESSION_TTL and not _session.get("stop", False):
                try:
                    # Memory-only preview; password is masked even if its eye toggle was used.
                    preview = page.screenshot(type="jpeg", quality=65,
                                              mask=[page.locator('input[type="password"], input[autocomplete="current-password"], input[data-psc-secret]')],
                                              timeout=5000)
                    with _lock:
                        _session["preview"] = preview
                except Exception:
                    pass
                page.wait_for_timeout(2000)
            browser.close()
            with _lock:
                if _session.get("started") == started:
                    _session.update(state="expired", browser=None, started=None, stop=False)
    except Exception as exc:
        with _lock:
            if _session.get("started") == started:
                _session.update(state="failed", browser=None, started=None, stop=False,
                                error=type(exc).__name__, stage=stage)


@app.after_request
def safe_headers(response):
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "clipboard-read=(), clipboard-write=()"
    return response


@app.get("/healthz")
def health():
    return jsonify(status="ok")


@app.get("/")
def home():
    return send_file("/srv/index.html", mimetype="text/html")



def _visual_connection_response(status_code=200):
    if not _session.get("resume_token"):
        _session["resume_token"] = secrets.token_urlsafe(32)
    remaining = max(0, int(SESSION_TTL - (time.monotonic() - _session["started"])))
    response = jsonify(status=_session["state"],
                       vnc_password=os.environ["SII_VISUAL_VNC_PASSWORD"],
                       expires_in_seconds=remaining)
    response.set_cookie("psc_visual_resume", _session["resume_token"],
                        max_age=remaining, secure=True, httponly=True, samesite="Strict")
    return response, status_code


def _can_resume():
    expected = _session.get("resume_token") or ""
    supplied = request.cookies.get("psc_visual_resume", "")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


@app.post("/api/resume")
def resume():
    with _lock:
        if not _can_resume():
            return jsonify(error="Vuelve al panel privado PSC para recuperar la sesión."), 403
        if _session.get("state") not in ("starting", "open"):
            return jsonify(error="La sesión terminó. Abre otra desde el panel PSC."), 410
        return _visual_connection_response()


@app.post("/api/start")
def start():
    body = request.get_json(silent=True) or {}
    ticket = body.get("ticket", "")
    if not isinstance(ticket, str) or len(ticket) > 2048:
        return jsonify(error="Enlace de sesión inválido."), 400
    data = _decode_ticket(ticket)
    if not data:
        return jsonify(error="El enlace venció o no es válido. Inicia la prueba otra vez desde el panel PSC."), 403

    with _lock:
        if data["nonce"] in _used_nonces:
            return jsonify(error="Este enlace ya fue utilizado."), 403
        if _session.get("state") in ("starting", "open"):
            _used_nonces.add(data["nonce"])
            return _visual_connection_response()
        _used_nonces.add(data["nonce"])

    try:
        credentials = _fetch_sii_credentials(ticket)
    except Exception as exc:
        app.logger.warning("SII_VISUAL_CREDENTIAL_FETCH_FAILED %s", type(exc).__name__)
        return jsonify(error="No se pudieron obtener las credenciales SII desde la API PSC."), 503

    with _lock:
        if _session.get("state") in ("starting", "open"):
            credentials.clear()
            return _visual_connection_response()
        _session.update(submitted=False, login_click_attempted=False, preview=None, field_check=None, resume_token=secrets.token_urlsafe(32), state="starting", error=None, stage="starting", stop=False, connectivity=None, started=time.monotonic(), browser=None)
        started = _session["started"]

    threading.Thread(target=_browser_owner, args=(started, credentials), daemon=True,
                     name="sii-visible-chromium").start()
    threading.Thread(target=_expire_session, args=(started,), daemon=True,
                     name="sii-visible-chromium-timeout").start()
    with _lock:
        return _visual_connection_response(202)


@app.post("/api/stop")
def stop():
    with _lock:
        if not _can_resume():
            return jsonify(error="Sesión no autorizada."), 403
        _session["stop"] = True
    return jsonify(status="stopping")


@app.get("/api/preview")
def preview():
    with _lock:
        if not _can_resume():
            return jsonify(error="Sesión no autorizada."), 403
        if _session.get("state") != "open" or not _session.get("preview"):
            return Response(status=204)
        return Response(_session["preview"], mimetype="image/jpeg")


@app.get("/api/status")
def status():
    return jsonify(status=_session["state"], error=_session.get("error"), stage=_session.get("stage"),
                   field_check=_session.get("field_check") if _can_resume() else None,
                   connectivity=_session.get("connectivity"),
                   expires_in_seconds=SESSION_TTL if _session.get("started") else 0)
