"""Prueba local de los dos primeros pasos de e-Boleta SII.

No emite boletas, no envía credenciales y nunca pulsa el botón EMITIR.
Requiere: pip install playwright ; python -m playwright install chromium

Ejemplo: python tools/sii_eboleta_pasos_1_2.py --monto 30000
"""
from __future__ import annotations

import argparse
import re
from decimal import Decimal, InvalidOperation

from playwright.sync_api import sync_playwright

SII_URL = "https://eboleta.sii.cl/emitir/"
SII_HOST = "eboleta.sii.cl"


def monto_valido(raw: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,8}", raw):
        raise argparse.ArgumentTypeError("Indica un monto entero positivo, sin puntos ni signo $.")
    return raw


def ingresar_digitos(pagina, monto: str) -> None:
    """Pulsar los números visibles sin intentar enviar ni emitir la boleta."""
    for digito in monto:
        # La pantalla del SII usa teclado numérico visual; no asumimos input editable.
        opciones = pagina.get_by_text(digito, exact=True)
        visibles = [opciones.nth(i) for i in range(opciones.count()) if opciones.nth(i).is_visible()]
        if len(visibles) != 1:
            raise RuntimeError(
                f"No puedo identificar con seguridad la tecla {digito!r} "
                f"({len(visibles)} coincidencias visibles). No se ha pulsado EMITIR. "
                "Hay que inspeccionar los selectores de la página."
            )
        visibles[0].click()
        pagina.wait_for_timeout(180)


def main() -> None:
    parser = argparse.ArgumentParser(description="Abrir e-Boleta y llenar monto SIN emitir.")
    parser.add_argument("--monto", required=True, type=monto_valido, help="Ejemplo: 30000")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(headless=False, slow_mo=120)
        contexto = navegador.new_context(locale="es-CL", timezone_id="America/Santiago")
        pagina = contexto.new_page()
        pagina.goto(SII_URL, wait_until="domcontentloaded", timeout=60000)
        print("1/2: Inicia sesión manualmente en el navegador del SII.")
        print("No ingreses tu clave en la consola ni la incluyas en el código.")
        input("Cuando veas el teclado numérico de e-Boleta, pulsa ENTER aquí... ")
        if pagina.url.split("/")[2].lower() != SII_HOST:
            raise RuntimeError("No estás en eboleta.sii.cl. Se cancela por seguridad.")
        if not pagina.get_by_text("EMITIR", exact=True).count():
            raise RuntimeError("No encuentro el botón EMITIR. La vista todavía no parece lista.")
        print("2/2: Ingresando monto con el teclado visual; NO pulsaré EMITIR.")
        ingresar_digitos(pagina, args.monto)
        print(f"Monto ingresado: {int(args.monto):,} CLP (sin emitir ninguna boleta).")
        print("Verifica visualmente que coincida con el monto deseado.")
        input("Pulsa ENTER en la consola para cerrar el navegador sin emitir... ")
        contexto.close()
        navegador.close()


if __name__ == "__main__":
    main()
