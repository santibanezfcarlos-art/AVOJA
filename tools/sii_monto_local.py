"""Prueba local de importe en e-Boleta, utilizando una sesión SII autorizada manualmente.

Puede pulsar el primer EMITIR con --continuar; no confirma etapas posteriores.
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
    parser.add_argument("--continuar", action="store_true", help="Pulsar primer EMITIR tras verificar el monto")
    parser.add_argument("--mostrar-campos", action="store_true", help="Activar Receptor y Detalle para verificar campos, sin rellenar")
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
            emitir = page.get_by_text(re.compile("emitir", re.I)).first
            try:
                page.get_by_text("7", exact=True).first.wait_for(state="visible", timeout=25000)
                if not any(emitir_candidate.is_visible() for emitir_candidate in [page.get_by_text(re.compile("emitir", re.I)).nth(i) for i in range(min(page.get_by_text(re.compile("emitir", re.I)).count(), 10))]):
                    raise RuntimeError("No se encontró un control visible de emisión.")
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
            page.wait_for_timeout(800)
            # e-Boleta puede dibujar "$ 30.000", "$30000" o usar espacios.
            # Leer solo importes del texto visible, sin imprimir otros datos.
            body = page.locator("body").inner_text(timeout=5000)
            candidates = re.findall(r"[$]\s*([0-9][0-9.,\s]*)", body)
            parsed = []
            for candidate in candidates:
                digits = re.sub(r"[^0-9]", "", candidate)
                if digits:
                    parsed.append(int(digits))
            confirmed = args.monto in parsed
            print("Navegador abrió e-Boleta. Teclas pulsadas:", len(str(args.monto)))
            print("Monto esperado: $" + format(args.monto, ",").replace(",", "."))
            print("Monto detectado en pantalla:", "SÍ" if confirmed else "NO CONFIRMADO")
            if not confirmed:
                print("Importes con símbolo $ detectados:", parsed[:5])
                print("No avanzaremos a EMITIR sin verificar visualmente el importe.")
            print("HTTP inicial:", response.status if response else "sin respuesta")
            if args.continuar and confirmed:
                # Priorizar controles interactivos del teclado, no nodos de texto
                # duplicados (por ejemplo span dentro de un botón).
                candidates = page.locator("button, [role='button'], input[type='button'], input[type='submit']")
                matches = []
                for i in range(min(candidates.count(), 100)):
                    candidate = candidates.nth(i)
                    if not candidate.is_visible() or not candidate.is_enabled():
                        continue
                    label = (candidate.get_attribute("value") or candidate.inner_text(timeout=500)).strip()
                    if re.fullmatch(r"emitir", label, re.I):
                        matches.append(candidate)
                if len(matches) != 1:
                    print("Controles interactivos EMITIR encontrados:", len(matches))
                    print("No se pulsó EMITIR: selector todavía ambiguo.")
                    return
                print("Importe validado; pulsando primer EMITIR del teclado...")
                matches[0].click(timeout=10000)
                page.wait_for_timeout(1800)
                print("Primer clic realizado. Dominio final:", urlparse(page.url).hostname)
                print("Botones visibles:", page.get_by_role("button").count())
                print("Campos de formulario:", page.locator("input").count())
                if args.mostrar_campos:
                    # Buscar las etiquetas visibles de los dos interruptores.
                    for text_label in ("Receptor", "Detalle"):
                        label = page.get_by_text(text_label, exact=True)
                        visible_labels = [label.nth(k) for k in range(min(label.count(), 12))
                                          if label.nth(k).is_visible()]
                        if len(visible_labels) != 1:
                            print("Etiqueta ambigua o ausente:", text_label, len(visible_labels))
                            return
                        switch = visible_labels[0].locator("xpath=preceding-sibling::*[1]")
                        # Intentar primero checkbox etiquetado accesiblemente por texto.
                        checkbox = page.get_by_role("checkbox", name=text_label, exact=True)
                        if checkbox.count() == 1:
                            if not checkbox.is_checked():
                                checkbox.check(timeout=5000)
                        else:
                            row = visible_labels[0].locator("xpath=..")
                            candidates = row.locator("input[type='checkbox']")
                            if candidates.count() != 1:
                                print("No se reconoce el interruptor de", text_label)
                                return
                            if not candidates.is_checked():
                                visible_labels[0].click(timeout=5000)
                        print("Interruptor activado:", text_label)
                    page.wait_for_timeout(700)
                    for placeholder in ("RUT con DV receptor", "Nombre receptor",
                                        "Dirección receptor", "E-mail receptor",
                                        "Teléfono receptor", "Detalle"):
                        loc = page.get_by_placeholder(placeholder, exact=True)
                        print("Campo", placeholder, "visible:",
                              any(loc.nth(k).is_visible() for k in range(min(loc.count(), 5))))
                    print("No se ingresaron datos ni se confirmó emisión.")
                print("Diagnóstico de campos visibles (sin valores):")
                for idx in range(min(page.locator("input, select, textarea").count(), 40)):
                    element = page.locator("input, select, textarea").nth(idx)
                    if not element.is_visible():
                        continue
                    field_type = element.get_attribute("type") or element.evaluate("(el) => el.tagName.toLowerCase()")
                    field_id = (element.get_attribute("id") or "")[:70]
                    field_name = (element.get_attribute("name") or "")[:70]
                    # Metadatos de interfaz únicamente, nunca valores escritos.
                    placeholder = (element.get_attribute("placeholder") or "")[:65]
                    aria = (element.get_attribute("aria-label") or "")[:65]
                    label = element.evaluate("""(el) => {
                      const own = el.labels && el.labels.length ? el.labels[0].innerText : "";
                      const parent = el.closest("label, .MuiFormControl-root, .form-group");
                      const adjacent = parent && parent.querySelector("label, legend");
                      return (own || (adjacent ? adjacent.innerText : "") || "").slice(0,65);
                    }""")
                    print("Campo", idx + 1, "tipo:", field_type, "id:", field_id,
                          "etiqueta:", repr(label), "placeholder:", repr(placeholder),
                          "aria:", repr(aria))
                print("Estructura de grupos del formulario (etiquetas, sin valores):")
                groups = page.locator("fieldset, [role='group'], .MuiFormControl-root")
                for gi in range(min(groups.count(), 30)):
                    group = groups.nth(gi)
                    if not group.is_visible():
                        continue
                    labels = group.locator("label, legend, .MuiFormLabel-root")
                    names = []
                    for li in range(min(labels.count(), 8)):
                        label_text = labels.nth(li).inner_text(timeout=500).strip()
                        if label_text and len(label_text) < 100 and label_text not in names:
                            names.append(label_text)
                    if names:
                        print("Grupo", gi + 1, ":", " | ".join(names))
                print("Nota: IDs numéricos de MUI pueden cambiar; usaremos etiquetas estables.")
                print("No se confirma ninguna etapa posterior.")
            else:
                print("EMITIR no fue pulsado.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
