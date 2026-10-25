"""Prueba aislada de Chromium en Render para PSC/Fonasa.

No usa contraseñas, no inicia sesión, no envía formularios.
Ejecutar manualmente en un entorno de prueba con:
    python psicologia-api/tools/fonasa_render_smoke.py
"""
from __future__ import annotations
import json
import os
import sys
from playwright.sync_api import sync_playwright, Error as PlaywrightError

URL = "https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80"
HOST = "bonowebptd.fonasa.cl"

def main() -> int:
    # No usar el perfil persistente del prestador durante una prueba de entorno.
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page()
                response = page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                result = {
                    "chromium_started": True,
                    "https_fonasa": page.url.startswith("https://" + HOST + "/"),
                    "http_status": response.status if response else None,
                    "login_form_present": page.locator('input[type="password"]').count() > 0,
                    "note": "No se ingresaron credenciales ni se envió formulario.",
                }
                print(json.dumps(result, ensure_ascii=False))
                return 0 if result["https_fonasa"] and result["login_form_present"] else 2
            finally:
                browser.close()
    except (PlaywrightError, OSError) as exc:
        # No registrar URLs completas, cabeceras o contenido de respuesta.
        print(json.dumps({"chromium_started": False, "error_type": type(exc).__name__,
                          "note": "Revisar dependencias de Chromium en el contenedor."}))
        return 1

if __name__ == "__main__":
    sys.exit(main())
