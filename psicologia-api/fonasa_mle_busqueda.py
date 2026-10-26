"""Extraccion del bono Fonasa y busqueda de Cuenta Medica en Bono Web.

Este modulo no inicia sesion ni almacena PDFs. La activacion requiere autorizacion explicita por folio; ninguna funcion se ejecuta automaticamente.
El caller debe ser un proceso servidor autenticado, nunca un endpoint publico
que permita buscar folios arbitrarios.
"""
import re
from urllib.parse import urlparse

from fonasa_parser import extraer_bono_pdf


class ConsultaBonoError(ValueError):
    pass


HOST_PERMITIDO = "bonowebptd.fonasa.cl"


def validar_rut(rut: str) -> str:
    """Normaliza un RUT chileno y verifica el digito verificador."""
    if not isinstance(rut, str):
        raise ConsultaBonoError("RUT del beneficiario invalido")
    cleaned = rut.replace(".", "").replace(" ", "").upper()
    if not re.fullmatch(r"\d{7,8}-[0-9K]", cleaned):
        raise ConsultaBonoError("Formato de RUT del beneficiario invalido")
    numero, digito = cleaned.split("-", 1)
    suma = sum(int(v) * (2 + i % 6) for i, v in enumerate(reversed(numero)))
    resto = 11 - suma % 11
    esperado = "0" if resto == 11 else "K" if resto == 10 else str(resto)
    if esperado != digito:
        raise ConsultaBonoError("Digito verificador del beneficiario invalido")
    return cleaned


def parametros_desde_pdf(pdf_bytes: bytes, *, rut_prestador: str,
                         codigo_prestacion: str = "0902002") -> dict:
    """Extrae el RUT BENEFICIARIO (no RUT titular) y el N° del bono.

    La verificacion contra Fonasa ocurre despues, pulsando Buscar; leer el PDF
    no demuestra que el bono sea autentico, vigente o no utilizado.
    """
    datos = extraer_bono_pdf(
        pdf_bytes, rut_prestador=rut_prestador, codigo=codigo_prestacion
    )
    rut = validar_rut(datos["rut_beneficiario"])
    folio = datos["folio"]
    if not re.fullmatch(r"\d{5,30}", folio):
        raise ConsultaBonoError("Folio invalido")
    return {
        "rut_beneficiario": rut,
        "folio": folio,
        "fecha_emision": datos["fecha_emision"],
        "codigo_prestacion": datos["codigo_prestacion"],
    }


def buscar_cuenta_medica(page, *, rut_beneficiario: str, folio: str) -> dict:
    """Completa RUT/Folio y pulsa Buscar en formulario ya autenticado.

    No ejecuta botones Activar, Desactivar o Guardar.
    No devuelve ni registra datos personales o contenido de la respuesta.
    """
    origin = urlparse(page.url)
    if origin.scheme != "https" or origin.hostname != HOST_PERMITIDO:
        raise ConsultaBonoError("Origen inesperado, busqueda cancelada")

    rut = validar_rut(rut_beneficiario)
    if not isinstance(folio, str) or not re.fullmatch(r"\d{5,30}", folio):
        raise ConsultaBonoError("Folio invalido")

    controles = {
        "rut": page.locator("#RutBeneficiario"),
        "folio": page.locator("#FolioBono"),
        "buscar": page.locator("#btnBuscar"),
    }
    for nombre, control in controles.items():
        if control.count() != 1 or not control.is_visible() or not control.is_enabled():
            raise ConsultaBonoError("Control de consulta no disponible: " + nombre)

    controles["rut"].fill(rut)
    controles["folio"].fill(folio)
    if controles["rut"].input_value() != rut or controles["folio"].input_value() != folio:
        raise ConsultaBonoError("Los datos no quedaron correctamente escritos")
    # Volver a validar el origen antes de emitir la consulta.
    if urlparse(page.url).hostname != HOST_PERMITIDO:
        raise ConsultaBonoError("Cambio de dominio antes de Buscar")

    controles["buscar"].click(timeout=7000)
    # Clic realizado NO implica que Fonasa haya encontrado o validado el bono.
    return {
        "buscar_pulsado": True,
        "bono_activado": False,
        "resultado_fonasa": "pendiente_de_inspeccion",
    }



def revisar_bono_en_resultados(page, *, rut_beneficiario: str, folio: str) -> dict:
    """Identifica UNA fila del bono buscado antes de considerar la activacion.

    BonoWeb puede mostrar el RUT del afiliado con ceros a la izquierda. La
    comparacion usa el RUT normalizado, nunca coincidencia parcial de texto.
    No abre detalles, no pulsa Activar ni interpreta la vigencia del bono.
    """
    if urlparse(page.url).scheme != "https" or urlparse(page.url).hostname != HOST_PERMITIDO:
        raise ConsultaBonoError("Origen inesperado")
    expected_rut = validar_rut(rut_beneficiario)
    if not isinstance(folio, str) or not re.fullmatch(r"\d{5,30}", folio):
        raise ConsultaBonoError("Folio invalido")

    def rut_de_pantalla(value):
        match = re.fullmatch(r"\s*0*(\d{7,8})-([0-9Kk])\s*", value)
        if not match:
            return None
        candidate = f"{match.group(1)}-{match.group(2).upper()}"
        try:
            return validar_rut(candidate)
        except ConsultaBonoError:
            return None

    encontrados = []
    for index in range(page.locator("table:visible tbody tr").count()):
        row = page.locator("table:visible tbody tr").nth(index)
        cols = row.locator("td").all_inner_texts()
        if len(cols) < 2:
            continue
        if cols[0].strip() == folio and rut_de_pantalla(cols[1]) == expected_rut:
            btn = row.get_by_role("button", name=re.compile(r"^\s*Activar\s*$", re.I))
            if btn.count() != 1 or not btn.is_visible() or not btn.is_enabled():
                raise ConsultaBonoError("La fila encontrada no permite Activar")
            encontrados.append((row, btn))
    if len(encontrados) != 1:
        raise ConsultaBonoError("No existe una fila unica para folio y beneficiario")
    return {
        "fila_unica_verificada": True,
        "boton_activar_disponible": True,
        "bono_activado": False,
        "requiere_confirmacion": True,
    }


def activar_bono_con_confirmacion(page, *, rut_beneficiario: str, folio: str,
                                 folio_confirmado: str, autorizar_activacion: bool = False) -> dict:
    """Accion financiera independiente: nunca se llama desde Buscar.

    Usar solo bajo autorizacion consciente del prestador para el folio exacto.
    El clic NO permite afirmar que la activacion/abono haya sido completado:
    se debe verificar por separado una respuesta oficial de BonoWeb.
    """
    if not autorizar_activacion or folio_confirmado != folio:
        raise ConsultaBonoError("Activacion no autorizada para el folio")
    revisar_bono_en_resultados(page, rut_beneficiario=rut_beneficiario, folio=folio)
    rut = validar_rut(rut_beneficiario)
    candidate = None
    rows = page.locator("table:visible tbody tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        cells = row.locator("td").all_inner_texts()
        if len(cells) < 2 or cells[0].strip() != folio:
            continue
        match = re.fullmatch(r"\s*0*(\d{7,8})-([0-9Kk])\s*", cells[1])
        if match and validar_rut(f"{match.group(1)}-{match.group(2)}") == rut:
            candidate = row
            break
    if candidate is None:
        raise ConsultaBonoError("Fila de bono ya no disponible")
    btn = candidate.get_by_role("button", name=re.compile(r"^\s*Activar\s*$", re.I))
    if btn.count() != 1 or not btn.is_visible() or not btn.is_enabled():
        raise ConsultaBonoError("Boton Activar no disponible")
    btn.click(timeout=7000)
    return {
        "clic_activar_enviado": True,
        "activacion_confirmada_por_fonasa": False,
        "resultado": "pendiente_verificacion_de_respuesta",
    }
