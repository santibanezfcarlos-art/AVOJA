"""Comprobación local de archivos de bonos antes del almacenamiento privado.

No autentica documentos ni consulta Fonasa. Requiere almacenamiento no público
y una ruta de reservas autenticada antes de admitir documentos de pacientes.
"""
import hashlib
import io
from pathlib import Path

MAX_SIZE = 5 * 1024 * 1024
TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}

class ComprobanteInvalido(ValueError):
    pass

def inspeccionar_comprobante(filename, content):
    """Devuelve metadatos seguros; no publica el contenido del documento."""
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename:
        raise ComprobanteInvalido("Nombre de archivo inválido")
    suffix = Path(filename).suffix.lower()
    if suffix not in TYPES:
        raise ComprobanteInvalido("Se admite PDF, PNG o JPG")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_SIZE:
        raise ComprobanteInvalido("Archivo vacío o mayor a 5 MB")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ComprobanteInvalido("No es un PDF válido")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ComprobanteInvalido("No es un PNG válido")
    if suffix in (".jpg", ".jpeg") and not (content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")):
        raise ComprobanteInvalido("No es un JPEG válido")
    return {"mime": TYPES[suffix], "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
