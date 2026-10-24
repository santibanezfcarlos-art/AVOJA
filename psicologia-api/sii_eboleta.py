"""Navegador invisible para validar inicio de sesión de e-Boleta SII.

No pulsa EMITIR, no genera documentos y no almacena sesión ni contraseñas.
La etapa de autenticación se controla con SII_AUTOMATION_ENABLED=false por defecto.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

SII_URL = "https://eboleta.sii.cl/emitir/"
ALLOWED_HOSTS = {"eboleta.sii.cl", "zeusr.sii.cl", "www.sii.cl", "sii.cl", "clave.w.sii.cl"}
RUT_PATTERN = re.compile(r"^\d{7,8}-[0-9Kk]$")


class SiiAutomationError(RuntimeError):
    pass


def preview_amount(amount: int) -> dict:
    if type(amount) is not int or not 1 <= amount <= 999_999_999:
        raise SiiAutomationError("Monto inválido")
    return {
        "url": SII_URL,
        "amount": amount,
        "document_type": 41,
        "headless": True,
        "configured": bool(os.getenv("SII_RUT") and os.getenv("SII_PASSWORD")),
        "steps": ["autenticar", "verificar EIRL", "ingresar monto"],
        "emitted": False,
    }


def _safe_host(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def _first_visible(page, selectors):
    for selector in selectors:
        elements = page.locator(selector)
        for index in range(min(elements.count(), 10)):
            item = elements.nth(index)
            if item.is_visible():
                return item
    return None


def normalize_rut(value: str) -> str:
    """Acepta RUT con puntos, guion o solo dígitos y DV, sin revelar su valor."""
    cleaned = value.strip().upper()
    for separator in (".", "-", " ", "\\t", "\\n", "‐", "‑", "‒", "–", "—", "−"):
        cleaned = cleaned.replace(separator, "")

    if not re.fullmatch(r"[0-9]{7,8}[0-9K]", cleaned):
        raise SiiAutomationError("SII_RUT tiene una longitud o caracteres inesperados.")
    return cleaned[:-1] + "-" + cleaned[-1]


def authenticate_sii() -> dict:
    """Intentar login server-side y retornar solo indicadores no sensibles.

    No se automatizan captcha, 2FA ni bloqueos interactivos. El proceso se
    detiene si cambia el formulario o la navegación sale de los hosts SII.
    """
    if os.getenv("SII_AUTOMATION_ENABLED", "false").lower() != "true":
        raise SiiAutomationError("Automatización SII deshabilitada en Render.")
    raw_rut = os.getenv("SII_RUT", "")
    password = os.getenv("SII_PASSWORD", "")
    if not raw_rut.strip():
        raise SiiAutomationError("SII_RUT no está disponible para este servicio.")
    rut = normalize_rut(raw_rut)
    if not password:
        raise SiiAutomationError("SII_PASSWORD no está disponible para este servicio.")

    # Browser binaries installed inside the application virtualenv survive Render builds.
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as exc:
        raise SiiAutomationError("Playwright no está instalado en el servidor.") from exc

    stage = "iniciar Playwright"
    try:
        with sync_playwright() as p:
            stage = "abrir Chromium"
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                stage = "crear sesión de navegador"
                ctx = browser.new_context(locale="es-CL", timezone_id="America/Santiago")
                page = ctx.new_page()
                page.set_default_timeout(12000)
                stage = "abrir portal SII"
                initial_response = page.goto(SII_URL, wait_until="domcontentloaded", timeout=30000)
                if not _safe_host(page.url):
                    raise SiiAutomationError("El portal redirigió fuera de los dominios oficiales del SII.")
                stage = "esperar redirección de autenticación SII"
                try:
                    page.wait_for_url("https://clave.w.sii.cl/**", timeout=15000)
                except PlaywrightTimeout:
                    pass
                if not _safe_host(page.url):
                    raise SiiAutomationError("Redirección fuera de los dominios oficiales del SII.")
                stage = "esperar formulario de acceso"
                try:
                    page.wait_for_load_state("networkidle", timeout=7000)
                except PlaywrightTimeout:
                    pass
                try:
                    page.locator('input[type="password"]').first.wait_for(state="visible", timeout=15000)
                except PlaywrightTimeout:
                    pass
                if page.url.startswith("https://eboleta.sii.cl/emitir"):
                    # Si ya hay sesión válida, no necesitamos volver a autenticar.
                    if page.get_by_text("Calculadora", exact=False).count() and page.get_by_text("EMITIR", exact=True).count():
                        return {"status": "authenticated", "emitted": False,
                                "message": "Sesión activa: se encontró la pantalla de ingreso de monto."}
                stage = "localizar formulario de acceso"
                rut_input = _first_visible(page, [
                    'input[name="rut"]', 'input[id="rut"]', 'input[name="RUT"]',
                    'input[autocomplete="username"]', 'input[placeholder*="RUT" i]',
                    'input[type="text"]',
                ])
                pass_input = _first_visible(page, [
                    'input[type="password"]', 'input[name="clave"]',
                ])
                if rut_input is None or pass_input is None:
                    from urllib.parse import urlparse
                    host = urlparse(page.url).hostname or "desconocido"
                    # Collect only counts/types, never values, page text, credentials or cookies.
                    inputs = page.locator("input")
                    input_types = {}
                    for index in range(min(inputs.count(), 25)):
                        field_type = (inputs.nth(index).get_attribute("type") or "text").lower()
                        if field_type not in ("text", "password", "hidden", "email", "number", "submit"):
                            field_type = "otro"
                        input_types[field_type] = input_types.get(field_type, 0) + 1
                    response = page.request.get(page.url, timeout=12000) if _safe_host(page.url) else None
                    http_status = response.status if response is not None else None
                    page_title = (page.title() or "").lower()
                    title_category = ("acceso" if any(word in page_title for word in ("acceso", "login", "autentic")) else
                                      "error" if any(word in page_title for word in ("error", "deneg", "prohib")) else "otro")
                    return {"status": "login_form_unrecognized", "emitted": False,
                            "message": "Página recibida desde SII; diagnóstico seguro de formulario.",
                            "diagnostic": {"host": host, "rut_found": rut_input is not None,
                                           "password_found": pass_input is not None,
                                           "input_types": input_types, "iframe_count": page.locator("iframe").count(),
                                           "http_status": http_status, "title_category": title_category,
                                           "dom_ready": page.evaluate("document.readyState")}}
                stage = "completar formulario de acceso"
                rut_input.fill(rut)
                pass_input.fill(password)
                submit = _first_visible(page, [
                    'button:has-text("Ingresar")',
                    'input[type="submit"]',
                    'button[type="submit"]',
                ])
                if submit is None:
                    return {"status": "login_button_unrecognized", "emitted": False,
                            "message": "No se identificó el botón de acceso al SII."}
                stage = "enviar formulario de acceso"
                submit.click()
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=12000)
                except PlaywrightTimeout:
                    pass
                if not _safe_host(page.url):
                    raise SiiAutomationError("Navegación fuera de SII; operación detenida.")
                # No inferimos sesión autenticada solo por haber pulsado Ingresar.
                return {"status": "login_submitted", "emitted": False,
                        "message": "Se envió el formulario. Debe verificarse el resultado en la siguiente etapa."}
            finally:
                browser.close()
    except SiiAutomationError:
        raise
    except Exception as exc:
        # No incluir el texto crudo de Playwright: puede contener secretos o URL de sesión.
        safe_kind = type(exc).__name__ if type(exc).__name__ in ("Error", "TimeoutError", "TargetClosedError") else "Error"
        if stage == "abrir Chromium":
            description = str(exc).lower()
            if "executable doesn\u0027t exist" in description or "please run the following command" in description:
                safe_kind = "NavegadorNoInstalado"
            elif "error while loading shared libraries" in description or "host system is missing dependencies" in description:
                safe_kind = "DependenciasSistema"
            elif "target page, context or browser has been closed" in description:
                safe_kind = "CierreDelNavegador"
        raise SiiAutomationError(f"Falló la etapa «{stage}» ({safe_kind}). No se emitió ninguna boleta.") from None
