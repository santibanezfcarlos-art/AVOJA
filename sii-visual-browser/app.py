"""Short-lived, visible SII Chromium session over noVNC.

The operator enters SII credentials directly in Chromium. This service never
accepts or stores them, and its browser blocks the final EMITIR action.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_file
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096
_lock = threading.Lock()
_used_nonces = set()
_session = {"state": "idle", "browser": None, "started": None}
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


def _proxy_settings():
    base = os.environ.get("SII_PROXY_WORKER_URL", "").rstrip("/")
    bridge = os.environ.get("SII_VISUAL_BRIDGE_TOKEN", "")
    if not base or len(bridge) < 32:
        raise RuntimeError("proxy bridge unavailable")
    response = requests.post(
        base + "/internal/sii-visual-proxy",
        headers={"X-SII-Visual-Bridge": bridge},
        json={},
        timeout=(5, 12),
    )
    if response.status_code != 200:
        raise RuntimeError("proxy bridge unavailable")
    payload = response.json()
    config = payload.get("proxy") if payload.get("status") == "ready" else None
    if not isinstance(config, dict):
        raise RuntimeError("proxy bridge unavailable")
    host = config["DECODO_PROXY_HOST"].strip()
    port = config["DECODO_PROXY_PORT"].strip()
    candidate = host if "://" in host else "http://" + host
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise RuntimeError("proxy settings invalid")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise RuntimeError("proxy settings invalid")
    server = f"{parsed.scheme}://{parsed.hostname}:{port}"
    return {
        "server": server,
        "username": config["DECODO_PROXY_USER"],
        "password": config["DECODO_PROXY_PASSWORD"],
    }


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


def _start_browser(proxy):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy=proxy,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            timeout=30000,
        )
    context = browser.new_context(locale="es-CL", timezone_id="America/Santiago", viewport={"width":1364,"height":768})
    context.add_init_script(BLOCK_ISSUE_ACTIONS)
    page = context.new_page()
    page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
    # Keep the Playwright objects alive in a dedicated owner thread.
    return browser


def _browser_owner(proxy, started):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False, proxy=proxy,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
                timeout=30000,
            )
            with _lock:
                if _session.get("started") != started:
                    browser.close()
                    return
                _session["browser"] = browser
                _session["state"] = "open"
            context = browser.new_context(locale="es-CL", timezone_id="America/Santiago",
                                          viewport={"width":1365,"height":768})
            context.add_init_script(BLOCK_ISSUE_ACTIONS)
            page = context.new_page()
            country_response = page.goto("https://ipinfo.io/country", wait_until="domcontentloaded", timeout=20000)
            country = (page.locator("body").inner_text(timeout=5000) or "").strip().upper()
            if country not in ("CL", "DE", "US", "GB", "FR", "SG", "NL"):
                country = "unknown"
            response = page.goto(TARGET, wait_until="domcontentloaded", timeout=45000)
            host = urlparse(page.url).hostname or ""
            safe_result = {"country": country,
                           "sii_http_status": response.status if response else None,
                           "final_host": host if host.endswith(".sii.cl") else "other"}
            with _lock:
                if _session.get("started") == started:
                    _session["connectivity"] = safe_result
                    _session["state"] = "open"
            app.logger.warning("SII_VISUAL_CONNECTIVITY %s", json.dumps(safe_result, separators=(",", ":")))
            while time.monotonic() - started < SESSION_TTL and not _session.get("stop", False):
                time.sleep(1)
            browser.close()
            with _lock:
                if _session.get("started") == started:
                    _session.update(state="expired", browser=None, started=None, stop=False)
    except Exception as exc:
        with _lock:
            if _session.get("started") == started:
                _session.update(state="failed", browser=None, started=None, stop=False,
                                error=type(exc).__name__)


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
            return jsonify(error="Ya hay una sesión visual activa. Espera a que termine."), 409
        _used_nonces.add(data["nonce"])
        _session.update(state="starting", error=None, stop=False, connectivity=None, started=time.monotonic(), browser=None)
        started = _session["started"]

    try:
        proxy = _proxy_settings()
    except Exception as exc:
        with _lock:
            _session.update(state="failed", started=None, error=type(exc).__name__)
        return jsonify(error="No se pudo conectar con la configuración de Decodo. Revisa el estado en el panel."), 503

    threading.Thread(target=_browser_owner, args=(proxy, started), daemon=True,
                     name="sii-visible-chromium").start()
    threading.Thread(target=_expire_session, args=(started,), daemon=True,
                     name="sii-visible-chromium-timeout").start()
    return jsonify(status="starting", vnc_password=os.environ["SII_VISUAL_VNC_PASSWORD"],
                   expires_in_seconds=SESSION_TTL), 202


@app.post("/api/stop")
def stop():
    with _lock:
        _session["stop"] = True
    return jsonify(status="stopping")


@app.get("/api/status")
def status():
    return jsonify(status=_session["state"], error=_session.get("error"), connectivity=_session.get("connectivity"),
                   expires_in_seconds=SESSION_TTL if _session.get("started") else 0)
