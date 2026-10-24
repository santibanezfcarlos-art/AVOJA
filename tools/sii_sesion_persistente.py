"""Prueba AISLADA de sesión persistente en e-Boleta; sin emitir documentos.

Ejecutar en una máquina propia o VPS con escritorio remoto.
Instalar: pip install playwright && python -m playwright install chromium
Uso:
  python tools/sii_sesion_persistente.py autorizar --perfil /ruta/privada/sii
  python tools/sii_sesion_persistente.py comprobar --perfil /ruta/privada/sii

El perfil contiene cookies/credenciales de sesión: NO subirlo a GitHub,
NO ubicarlo en una carpeta pública y mantener acceso restringido.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

PORTAL = "https://eboleta.sii.cl/emitir/"
HOSTS = {"eboleta.sii.cl", "clave.w.sii.cl"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=("autorizar", "comprobar"))
    ap.add_argument("--perfil", type=Path, required=True)
    args = ap.parse_args()
    profile = args.perfil.expanduser().resolve()
    if not profile.is_absolute() or str(profile) in ("/", str(Path.cwd())):
        ap.error("Elige una carpeta privada específica para el perfil.")
    profile.mkdir(parents=True, exist_ok=True)
    try:
        profile.chmod(0o700)
    except OSError:
        pass
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile), headless=(args.accion == "comprobar"),
            locale="es-CL", timezone_id="America/Santiago",
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            response = page.goto(PORTAL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            host = urlparse(page.url).hostname
            if host not in HOSTS:
                print("Se detectó otro dominio; no se continúa.")
                return
            if args.accion == "autorizar":
                print("Navegador visible: inicia sesión tú mismo en el SII.")
                print("No pulses EMITIR. Cuando veas la pantalla del monto, vuelve a la consola.")
                input("Presiona ENTER para guardar y cerrar la sesión local...")
                print("Perfil conservado en carpeta privada.")
            else:
                on_amount_page = (host == "eboleta.sii.cl" and
                                  page.get_by_text("EMITIR", exact=True).count() > 0)
                print("Sesión reutilizable:", "SÍ" if on_amount_page else "NO")
                print("Dominio final:", host)
                print("HTTP inicial:", response.status if response else "sin respuesta")
                print("No se introdujo ningún monto ni se emitió documento.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
