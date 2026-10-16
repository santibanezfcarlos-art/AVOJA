"""Verification emails over HTTPS. No development shortcuts or email auto-approval."""
import json
import os
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class EmailDeliveryError(RuntimeError):
    def __init__(self, category, http_status=None):
        self.category, self.http_status = category, http_status
        super().__init__('No se pudo enviar el correo. Administración puede revisar el diagnóstico del servicio.')


class EmailDelivery:
    def __init__(self):
        self.provider = os.environ.get('VALHALLA_EMAIL_PROVIDER', 'sendgrid').strip().lower()
        self.api_key = os.environ.get('VALHALLA_SENDGRID_API_KEY' if self.provider == 'sendgrid' else 'VALHALLA_RESEND_API_KEY', '').strip()
        self.sender = os.environ.get('VALHALLA_EMAIL_FROM', '').strip()
        self.public_url = os.environ.get('VALHALLA_PUBLIC_URL', 'https://valhalla-office-demo.onrender.com').strip().rstrip('/')

    @property
    def configured(self):
        try:
            parsed = urlsplit(self.public_url)
            return bool(self.provider in ('sendgrid', 'resend') and self.api_key and '@' in parseaddr(self.sender)[1] and '\n' not in self.sender and '\r' not in self.sender and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment and not parsed.query and parsed.path in ('', '/'))
        except ValueError:
            return False

    def diagnostics(self):
        return {'provider':self.provider if self.provider in ('resend','sendgrid') else 'invalid',
                'sender':self.sender, 'public_url':self.public_url,
                'api_key_present':bool(self.api_key), 'configured':self.configured}

    def send_test(self, email):
        self.send_message(email, 'Prueba de correo · Valhalla Office',
                          'Este es un mensaje de prueba solicitado desde la administración de Valhalla Office.\n'
                          'Si lo recibiste, el servicio de envío aceptó este mensaje y tu buzón lo recibió.\n'
                          'Esta prueba no confirma cuentas, no cambia contraseñas y no contiene enlaces de acceso.')

    def send_verification(self, email, token):
        if not self.configured:
            raise RuntimeError('El servicio de correo todavía no está configurado.')
        link = self.public_url + '/?verify_email=' + token
        text = ('Confirma tu correo para usar tu cuenta de Valhalla Office.\n\n' + link +
                '\n\nEl enlace vence en 24 horas y se usa una sola vez. Si no creaste esta cuenta, ignora este mensaje.')
        self.send_message(email, 'Confirma tu correo · Valhalla Office', text)

    def send_password_reset(self, email, token):
        link = self.public_url + '/?reset_password=' + token
        text = ('Solicitaste cambiar tu contraseña de Valhalla Office.\n\n' + link +
                '\n\nEl enlace vence en 30 minutos y se usa una sola vez. Si no lo solicitaste, ignora este mensaje; tu contraseña no cambiará.')
        self.send_message(email, 'Recupera tu contraseña · Valhalla Office', text)

    def send_message(self, email, subject, text):
        if not self.configured:
            raise RuntimeError('El servicio de correo todavía no está configurado.')
        if self.provider == 'sendgrid':
            name, address = parseaddr(self.sender)
            endpoint = 'https://api.sendgrid.com/v3/mail/send'
            payload = {'personalizations': [{'to': [{'email': email}]}],
                       'from': {'email': address, 'name': name or 'Valhalla Office'},
                       'subject': subject, 'content': [{'type': 'text/plain', 'value': text}]}
        else:
            endpoint = 'https://api.resend.com/emails'
            payload = {'from': self.sender, 'to': [email], 'subject': subject, 'text': text}
        request = Request(endpoint, method='POST',
                          headers={'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json', 'User-Agent':'ValhallaOffice/1.0 (transactional-email)'},
                          data=json.dumps(payload).encode())
        try:
            with urlopen(request, timeout=10) as response:
                if self.provider == 'sendgrid':
                    if response.status != 202:
                        raise EmailDeliveryError('unexpected_response', response.status)
                    return
                data = json.load(response)
                if not isinstance(data, dict) or not isinstance(data.get('id'),str) or not data['id']:
                    raise EmailDeliveryError('unexpected_response')
        except HTTPError as error:
            # Fixed categories only. Provider bodies can include addresses, keys or links.
            category = {400:'invalid_request',401:'invalid_key',403:'permissions_or_domain',
                        404:'provider_endpoint',422:'invalid_sender_or_payload',429:'rate_limited'}.get(error.code,'provider_unavailable' if error.code>=500 else 'provider_rejected')
            status=error.code
            error.close()
            raise EmailDeliveryError(category,status) from None
        except (TimeoutError, URLError, OSError):
            raise EmailDeliveryError('network_error') from None
        except (ValueError, TypeError):
            raise EmailDeliveryError('unexpected_response') from None
