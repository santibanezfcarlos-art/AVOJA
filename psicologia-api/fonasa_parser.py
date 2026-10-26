"""Lectura de bonos Fonasa PDF con texto seleccionable.

No verifica autenticidad, vigencia ni uso ante Fonasa.
No almacena documentos ni registra datos clínicos.
Dependencia opcional para este módulo: PyMuPDF.
"""
import re

class BonoInvalido(ValueError):
    pass

def extraer_bono_pdf(content: bytes, *, rut_prestador: str, codigo: str = "0902002"):
    import fitz
    if not isinstance(content, bytes) or not 0 < len(content) <= 5 * 1024 * 1024:
        raise BonoInvalido("Archivo vacío o mayor a 5 MB")
    try:
        with fitz.open(stream=content, filetype="pdf") as doc:
            if len(doc) != 1:
                raise BonoInvalido("Se esperaba un bono de una página")
            texto = doc[0].get_text()
    except BonoInvalido:
        raise
    except Exception:
        raise BonoInvalido("No se pudo leer el PDF") from None
    if "FONDO NACIONAL DE SALUD" not in texto or "BONO DE ATENCION DE SALUD" not in texto:
        raise BonoInvalido("Documento no reconocido como bono Fonasa")
    def buscar(pattern, label):
        match = re.search(pattern, texto, re.M | re.I)
        if not match:
            raise BonoInvalido("No se pudo leer " + label)
        return match.group(1).strip()
    folio = buscar(r"BONO DE ATENCION DE SALUD\s*N[°º]\s*(\d{5,30})", "folio")
    emitido = buscar(r"FECHA EMISION\s*:\s*(\d{2}/\d{2}/\d{4})", "fecha emisión")
    beneficiario = buscar(r"RUT PERSONA BENEFICIARIO\(A\)\s*:\s*([\d.]+-[\dkK])", "beneficiario")
    prestador = buscar(r"PROFESIONAL/INSTITUCION\s*:\s*([\d.]+-[\dkK])", "prestador")
    if prestador.replace(".", "").upper() != rut_prestador.replace(".", "").upper():
        raise BonoInvalido("Bono de otro prestador")
    if not re.search(r"(?m)^" + re.escape(codigo) + r"\s*$", texto):
        raise BonoInvalido("Código de prestación distinto")
    copago = buscar(r"MONTO A PAGAR\s*:\s*([\d.]+)", "copago")
    return {"folio": folio, "fecha_emision": emitido,
            "rut_beneficiario": beneficiario, "rut_prestador": prestador,
            "codigo_prestacion": codigo, "copago_clp": int(copago.replace(".", ""))}
