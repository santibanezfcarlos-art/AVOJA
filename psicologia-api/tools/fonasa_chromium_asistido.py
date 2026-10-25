"""Asistente local de consulta de bonos MLE: NO activa bonos.

Ejecutar con Python 3.12 y Playwright. No almacenar claves en código.
Se detiene antes de pulsar Buscar o Activar.
"""
import argparse
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80"
HOST = "bonowebptd.fonasa.cl"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rut", required=True)
    parser.add_argument("--folio", required=True)
    parser.add_argument("--perfil", default=str(Path.home() / "PSC-Fonasa" / "perfil"))
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{7,8}-[0-9Kk]", args.rut):
        parser.error("RUT: use el formato 12345678-9")
    if not re.fullmatch(r"[0-9]{5,30}", args.folio):
        parser.error("Folio numérico inválido")
    profile = Path(args.perfil).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile), headless=False, channel="chrome")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        print("Si es necesario, inicie sesión manualmente en Fonasa.")
        print("Navegue a: Activación y desactivar Cuenta Médica > Activación Cuenta Médica.")
        input("Una vez que vea el formulario RUT Beneficiario / Folio Bono, presione ENTER aquí...")
        if not page.url.startswith("https://" + HOST + "/"):
            raise RuntimeError("La pestaña no pertenece al portal oficial de Fonasa")
        # Los labels visibles en las capturas son RUT Beneficiario y Folio Bono.
        labels = page.locator("input:not([type=hidden])")
        # En caso de un cambio del portal, evitar completar otros campos.
        try:
            rut_field = page.get_by_text("Rut Beneficiario", exact=False).locator("xpath=following::input[1]")
            folio_field = page.get_by_text("Folio Bono", exact=False).locator("xpath=following::input[1]")
            if rut_field.count() != 1 or folio_field.count() != 1:
                raise RuntimeError("Campos ambiguos")
            rut_field.fill(args.rut)
            folio_field.fill(args.folio)
        except Exception as exc:
            raise RuntimeError("No se pudo identificar el formulario de manera inequívoca; no se hizo la búsqueda") from exc
        print("RUT y folio completados. Revise los valores en pantalla.")
        print("El programa NO pulsa Buscar, Activar ni Desactivar.")
        input("Cuando termine la comprobación manual, presione ENTER para cerrar...")
        context.close()

if __name__ == "__main__":
    main()
