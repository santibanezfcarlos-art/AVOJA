"""Ejecución server-side de e-Boleta: primera etapa, acceso e ingreso de monto.
Nunca confirma EMITIR mientras no se hayan validado los selectores reales.
No registrar contraseñas, cookies, HTML ni datos de pacientes en logs.
"""
import os
import re

SII_URL = "https://eboleta.sii.cl/emitir/"

class SiiAutomationError(RuntimeError):
    pass

def preview_amount(amount: int) -> dict:
    """Especificación segura para el futuro ejecutor Playwright."""
    if not isinstance(amount, int) or not 1 <= amount <= 999_999_999:
        raise SiiAutomationError("Monto inválido")
    return {
        "url": SII_URL,
        "amount": amount,
        "document_type": 41,
        "headless": True,
        "configured": bool(os.getenv("SII_RUT") and os.getenv("SII_PASSWORD")),
        "steps": ["autenticar", "verificar EIRL", "ingresar monto", "emitir"],
        "emitted": False,
    }
