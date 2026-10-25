"""Consulta asistida Fonasa MLE por Chromium/Playwright.

Credenciales: variables de entorno FONASA_MLE_USER y FONASA_MLE_PASSWORD,
configuradas exclusivamente en el entorno que EJECUTA el navegador.
Nunca guardarlas en GitHub, argumentos de línea de comandos ni logs.
No busca ni activa bonos: deja el RUT del beneficiario y folio preparados.
"""
import argparse
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80"
HOST = "bonowebptd.fonasa.cl"

def fill_unique(locator, value, description):
    if locator.count() != 1:
        raise RuntimeError(f"No se identificó de forma única el campo {description}")
    locator.fill(value)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rut", required=True, help="RUT del beneficiario, no del prestador")
    parser.add_argument("--folio", required=True)
    parser.add_argument("--perfil", default=str(Path.home() / "PSC-Fonasa" / "perfil"))
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{7,8}-[0-9Kk]", args.rut):
        parser.error("RUT: use el formato 12345678-9")
    if not re.fullmatch(r"[0-9]{5,30}", args.folio):
        parser.error("Folio numérico inválido")
    username = os.environ.get("FONASA_MLE_USER", "").strip()
    password = os.environ.get("FONASA_MLE_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("Configure FONASA_MLE_USER y FONASA_MLE_PASSWORD en el entorno de ejecución")
    profile = Path(args.perfil).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile), headless=False, channel="chrome")
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            if not page.url.startswith("https://" + HOST + "/"):
                raise RuntimeError("Origen inesperado: se cancela el ingreso de credenciales")
            # Ajustar selectores contra HTML real después de la primera prueba.
            user_field = page.locator('input[name="usuario"], input[name="user"], input[id="usuario"]')
            pass_field = page.locator('input[type="password"]')
            if pass_field.count() == 1:
                fill_unique(user_field, username, "usuario")
                fill_unique(pass_field, password, "contraseña")
                login = page.get_by_role("button", name=re.compile(r"^Ingresar$", re.I))
                if login.count() != 1:
                    login = page.locator('input[type="submit"][value="Ingresar"]')
                if login.count() != 1:
                    raise RuntimeError("No se identificó de forma única el botón Ingresar")
                login.click()
                page.wait_for_load_state("domcontentloaded")
                print("Se pulsó Ingresar. Verifique que la sesión quedó iniciada.")
            else:
                print("No se encontró el login; puede existir una sesión ya iniciada.")
            print("Abra 'Activación y desactivar Cuenta Médica' > 'Activación Cuenta Médica'.")
            input("Con el formulario RUT Beneficiario / Folio Bono visible, presione ENTER...")
            if not page.url.startswith("https://" + HOST + "/"):
                raise RuntimeError("Origen inesperado: se cancela")
            rut_field = page.get_by_text("Rut Beneficiario", exact=False).locator("xpath=following::input[1]")
            folio_field = page.get_by_text("Folio Bono", exact=False).locator("xpath=following::input[1]")
            fill_unique(rut_field, args.rut, "RUT Beneficiario")
            fill_unique(folio_field, args.folio, "Folio Bono")
            print("Campos completados. El programa NO pulsa Buscar, Activar ni Desactivar.")
            input("Cuando termine la revisión manual, presione ENTER para cerrar...")
        finally:
            context.close()

if __name__ == "__main__":
    main()
