"""Responsive transactional access email using the official Valhalla Office logo.

The image is the same publicly served asset used by the existing booking emails.
Text-only fallback remains the original TTLock message.
"""
from datetime import timedelta
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from ttlock_daily import CHILE

MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def access_email_html(code, issued_at, public_url="https://valhalla-office-demo.onrender.com"):
    """Email-safe tables + inline styles; public HTTPS logo or a text fallback."""
    import re
    if not re.fullmatch(r"[0-9]{4,12}", str(code)):
        raise ValueError("Invalid TTLock code")
    if issued_at.tzinfo is None:
        raise ValueError("issued_at must be timezone-aware")
    booking_date = issued_at.astimezone(CHILE).date() if issued_at.astimezone(CHILE).hour == 5 else issued_at.astimezone(CHILE).date() + timedelta(days=1)
    date_label = f"{booking_date.day} de {MONTHS[booking_date.month-1]} de {booking_date.year}"
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("A public HTTPS URL is required for the logo")
    logo_file = Path(__file__).parent / "static" / "images" / "valhalla-logo.png"
    logo = (
        '<img src="' + escape(public_url.rstrip("/") + "/images/valhalla-logo.png", quote=True) +
        '" width="200" alt="Valhalla Office" style="display:block;width:200px;max-width:100%;height:auto;border:0;margin:0 auto;">'
        if logo_file.is_file() else
        '<span style="font-family:Georgia,serif;font-size:26px;letter-spacing:3px;color:#193d35;">VALHALLA OFFICE</span>'
    )
    safe_code = escape(str(code))
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Código de acceso · Valhalla Office</title></head>
<body style="margin:0;padding:0;background:#f3f5f2;font-family:Arial,Helvetica,sans-serif;color:#35483e;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f2;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:580px;background:#f3f5f2;border:1px solid #d9e3dd;border-radius:14px;">
<tr><td align="center" bgcolor="#ffffff" style="padding:24px 20px 18px;">
{logo}
<p style="font-size:11px;color:#627669;letter-spacing:2px;margin:12px 0 0;">OFFICE &amp; WELLBEING</p>
</td></tr>
<tr><td align="center" bgcolor="#193d35" style="padding:14px;color:#ffffff;font-size:13px;letter-spacing:1px;font-weight:bold;">ACCESO A INSTALACIONES</td></tr>
<tr><td style="padding:24px 26px 26px;">
<h1 style="font-size:24px;line-height:30px;text-align:center;color:#193d35;margin:0 0 24px;">Tu código de acceso</h1>
<p style="font-size:15px;line-height:24px;margin:0 0 12px;"><strong>Estimado/a profesional:</strong></p>
<p style="font-size:15px;line-height:24px;margin:0 0 24px;">Junto con saludar, compartimos el código de ingreso a las instalaciones de <strong>Valhalla Office</strong>:</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#ffffff;border:1px solid #d4e1d8;border-radius:12px;">
<tr><td align="center" style="padding:25px 12px;">
<p style="font-size:12px;color:#61766b;font-weight:bold;letter-spacing:1px;margin:0 0 12px;">CÓDIGO DE ACCESO</p>
<p style="font-size:35px;line-height:42px;color:#193d35;font-weight:bold;letter-spacing:2px;margin:0 0 10px;">#{safe_code}#</p>
<p style="font-size:14px;color:#60776b;margin:0;">&#128197; {escape(date_label)}</p>
</td></tr></table>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:20px;background:#e3ece5;border-radius:10px;">
<tr><td style="padding:20px;">
<p style="font-size:15px;font-weight:bold;color:#193d35;line-height:22px;margin:0 0 12px;">Importante: código personal e intransferible</p>
<p style="font-size:14px;line-height:22px;margin:0 0 12px;">Este código es de uso exclusivo del profesional que tiene una reserva confirmada en Valhalla Office.</p>
<p style="font-size:14px;line-height:22px;margin:0 0 12px;"><strong>Está prohibido compartirlo, reenviarlo o facilitarlo a pacientes, usuarios de las instalaciones, colegas, otros profesionales o terceros.</strong></p>
<p style="font-size:14px;line-height:22px;margin:0;">Por seguridad, solicitamos mantener su confidencialidad y utilizarlo únicamente para el ingreso autorizado.</p>
</td></tr></table>
<p style="font-size:12px;line-height:20px;text-align:center;color:#68796e;margin:22px 0 0;">Este es un mensaje automático. Por favor, no respondas a este correo.</p>
</td></tr>
<tr><td align="center" bgcolor="#e0e9e3" style="padding:18px;">
<p style="font-size:14px;color:#193d35;font-weight:bold;margin:0 0 6px;">VALHALLA OFFICE</p>
<p style="font-size:12px;color:#61766b;margin:0 0 6px;">Office &amp; Wellbeing</p>
<p style="font-size:12px;color:#61766b;margin:0;">contacto@valhallaoffice.cl</p>
</td></tr>
</table></td></tr></table></body></html>"""
