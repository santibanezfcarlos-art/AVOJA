"""Extraccion del bono Fonasa y busqueda de Cuenta Medica en Bono Web.

Este modulo no inicia sesion, no almacena PDFs y no activa ni desactiva bonos.
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
