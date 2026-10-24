"""Prueba local de importe en e-Boleta, utilizando una sesión SII autorizada manualmente.

NO pulsa EMITIR, no introduce datos de receptor, no envía documentos.
Ejemplo:
  python sii_monto_local.py --perfil "$env:USERPROFILE\\PSC-SII\\perfil-privado" --monto 30000
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

PORTAL = "https://eboleta.sii.cl/emitir/"


def amount(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
        raise argparse.ArgumentTypeError("Monto positivo, entero, sin separadores (ejemplo: 30000).")
    return int(value)


def exactly_one_visible(page, digit: str):
    # Buscar primero controles accesibles; no pulsar si hay ambigüedad.
    matching = page.get_by_role("button", name=digit, exact=True)
    found = [matching.nth(i) for i in range(matching.count()) if matching.nth(i).is_visible()]
    if len(found) != 1:
        # Algunos teclados SII están implementados con divs y no buttons.
        matching = page.get_by_text(digit, exact=True)
        found = [matching.nth(i) for i in range(matching.count()) if matching.nth(i).is_visible()]
    if len(found) != 1:
        raise RuntimeError(f"No fue posible identificar una única tecla {digit}. Se cancela.")
    return found[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--perfil", type=Path, required=True)
    parser.add_argument("--monto", type=amount, required=True)
    parser.add_argument("--visible", action="store_true", help="Diagnóstico con navegador visible")
    args = parser.parse_args()
    profile = args.perfil.expanduser().resolve()
    if not profile.is_dir():
        parser.error("No encuentro el perfil de la sesión autorizada. Ejecute primero el script 'autorizar'.")
    with sync_playwright() as playwright:
        ctx = playwright.chromium.launch_persistent_context(
            str(profile), headless=not args.visible, locale="es-CL", timezone_id="America/Santiago"
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            response = page.goto(PORTAL, wait_until="domcontentloaded", timeout=45000)
            # El texto de un botón puede ser "Emitir" aunque CSS lo muestre
            # como "EMITIR"; identificar sin depender de mayúsculas exactas.
            emitir = page.get_by_text(re.compile(r"^\\s*emitir\\s*$", re.I)).first
            try:
                emitir.wait_for(state="visible", timeout=25000)
            except Exception:
                print("No se confirmó la pantalla de monto del SII.")
                print("Dominio final:", urlparse(page.url).hostname or "desconocido")
                print("HTTP inicial:", response.status if response else "sin respuesta")
                print("¿Texto emitir (cualquier mayúscula)?:",
                      page.get_by_text(re.compile("emitir", re.I)).count() > 0)
                print("¿Tecla numérica 7?:", page.get_by_text("7", exact=True).count() > 0)
                print("¿Campo de clave?:", page.locator('input[type="password"]').count() > 0)
                print("No se ingresó ningún monto ni se emitió una boleta.")
                return
            if urlparse(page.url).hostname != "eboleta.sii.cl":
                raise RuntimeError("Sesión vencida o pantalla no autorizada.")
            # Para comenzar desde cero, localizar la tecla de papelera.
            # No hacemos clic a ciegas: si ya hay monto, se cancela antes de introducir dígitos.
            display = page.locator("input[readonly]").first
            if display.count() and display.is_visible():
                value = display.input_value()
                if value and re.sub(r"\\D", "", value) not in ("", "0"):
                    raise RuntimeError("Existe un monto previo; borra el valor manualmente antes de probar.")
            if args.visible:
                print("Diagnóstico visible: se encontró la pantalla del monto. No se pulsarán teclas.")
                input("Presiona ENTER para cerrar el navegador sin introducir monto...")
                return
            for digit in str(args.monto):
                exactly_one_visible(page, digit).click()
                page.wait_for_timeout(120)
            # La verificación final requiere un selector probado del campo visual.
            # Nunca informar como verificado si no se puede leer.
            body = page.locator("body").inner_text(timeout=5000)
            money = "$" + format(args.monto, ",").replace(",", ".")
            print("Navegador abrió e-Boleta. Teclas pulsadas:", len(str(args.monto)))
            print("Monto esperado:", money)
            print("Monto detectado en pantalla:", "SÍ" if money in body else "NO / formato diferente")
            print("HTTP inicial:", response.status if response else "sin respuesta")
            print("EMITIR no fue pulsado; no se generó ninguna boleta.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
