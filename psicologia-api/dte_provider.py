"""OpenFactura DTE 41. No network at import; explicit environment selection."""
import base64
import json
import os
import re
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

class ProviderError(RuntimeError):
    pass

class Uncertain(ProviderError):
    pass

class Duplicate(ProviderError):
    def __init__(self, token):
        self.token = token

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def payload(order, issuer, day):
    if type(order['amount']) is not int or order['amount'] <= 0:
        raise ValueError('Monto inválido')
    required = ('RUTEmisor', 'RznSocEmisor', 'GiroEmisor', 'DirOrigen', 'CmnaOrigen')
    if any(not issuer.get(k) for k in required):
        raise ValueError('Faltan datos tributarios del emisor')
    return {'response': ['PDF', 'FOLIO', 'XML'], 'dte': {
        'Encabezado': {
            'IdDoc': {'TipoDTE': 41, 'Folio': 0, 'FchEmis': day, 'IndServicio': 3},
            'Emisor': issuer,
            'Receptor': {'RUTRecep': order['rut'], 'RznSocRecep': order['name'],
                         'DirRecep': order['address'], 'Contacto': order['phone']},
            'Totales': {'MntExe': order['amount'], 'MntTotal': order['amount'],
                        'TotalPeriodo': order['amount'], 'VlrPagar': order['amount']}},
        'Detalle': [{'NroLinDet': 1, 'IndExe': 1, 'NmbItem': order['detail'],
                     'QtyItem': 1, 'PrcItem': order['amount'], 'MontoItem': order['amount']}]}}


def pdf_bytes(value):
    if not isinstance(value, str) or len(value) > 8_000_000:
        raise Uncertain('PDF inválido')
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        raise Uncertain('PDF inválido') from None
    if not raw.startswith(b'%PDF-'):
        raise Uncertain('PDF inválido')
    return raw


class OpenFactura:
    def __init__(self, environment):
        if environment not in ('integration', 'production'):
            raise ProviderError('Ambiente inválido')
        self.environment = environment
        if environment == 'production':
            if os.getenv('PSC_DTE_PRODUCTION_APPROVED') != 'true':
                raise ProviderError('Emisión productiva deshabilitada')
            self.base = 'https://api.haulmer.com/v2/dte/document'
            self.key = os.getenv('PSC_DTE_API_KEY', '')
            if self.key in ('928e15a2d14d4a6292345f04960f4bd3', '41eb78998d444dbaa4922c410ef14057'):
                raise ProviderError('Una clave pública de prueba no sirve para producción')
        else:
            self.base = 'https://dev-api.haulmer.com/v2/dte/document'
            self.key = os.getenv('PSC_DTE_SANDBOX_API_KEY', '')
        if not self.key:
            raise ProviderError('Falta API key del ambiente')
        self.opener = build_opener(NoRedirect())

    def _call(self, method, suffix='', body=None, key=None):
        headers = {'Content-Type': 'application/json', 'apikey': self.key}
        if key:
            headers['Idempotency-Key'] = key
        req = Request(self.base + suffix, method=method, headers=headers,
                      data=json.dumps(body).encode() if body else None)
        try:
            with self.opener.open(req, timeout=25) as response:
                result = json.loads(response.read(12_000_001))
        except HTTPError as exc:
            try:
                result = json.loads(exc.read(16384))
                error = result.get('error', {})
                if error.get('code') == 'OF-06' and re.fullmatch(r'[a-fA-F0-9]{64}', error.get('token', '')):
                    raise Duplicate(error['token'])
            except (ValueError, AttributeError):
                pass
            if exc.code in (400, 401, 403, 422):
                raise ProviderError('Proveedor rechazó la solicitud; revisar configuración') from None
            raise Uncertain('Respuesta incierta del proveedor') from None
        except (OSError, ValueError):
            raise Uncertain('Respuesta incierta del proveedor') from None
        if not isinstance(result, dict):
            raise Uncertain('Respuesta inválida')
        return result

    def issue(self, body, key):
        return self._call('POST', body=body, key=key)

    def get(self, token, value):
        if not re.fullmatch(r'[a-fA-F0-9]{64}', token) or value not in ('json', 'pdf', 'status', 'xml'):
            raise ProviderError('Identificador inválido')
        return self._call('GET', '/' + token + '/' + value)
