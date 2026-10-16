"""Verification emails over HTTPS. No development shortcuts or email auto-approval."""
import json
import os
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class EmailDelivery:
    def __init__(self):
        self.provider = os.environ.get('VALHALLA_EMAIL_PROVIDER', 'sendgrid').lower()
        self.api_key = os.environ.get('VALHALLA_SENDGRID_API_KEY' if self.provider == 'sendgrid' else 'VALHALLA_RESEND_API_KEY', '')
        self.sender = os.environ.get('VALHALLA_EMAIL_FROM', '')
        self.public_url = os.environ.get('VALHALLA_PUBLIC_URL', 'https://valhalla-office-demo.onrender.com').rstrip('/')

    @property
    def configured(self):
        parsed = urlsplit(self.public_url)
        return bool(self.provider in ('sendgrid', 'resend') and self.api_key and '@' in parseaddr(self.sender)[1] and '\n' not in self.sender and '\r' not in self.sender and parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.fragment and not parsed.query)

    def send_verification(self, email, token):
        if not self.configured:
            raise RuntimeError('El servicio de correo todavía no está configurado.')
        link = self.public_url + '/?verify_email=' + token
        text = ('Confirma tu correo para usar tu cuenta de Valhalla Office.\n\n' + link +
                '\n\nEl enlace vence en 24 horas y se usa una sola vez. Si no creaste esta cuenta, ignora este mensaje.')
        subject = 'Confirma tu correo · Valhalla Office'
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
                          headers={'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'},
                          data=json.dumps(payload).encode())
        try:
            with urlopen(request, timeout=10) as response:
                if self.provider == 'sendgrid':
                    if response.status != 202:
                        raise RuntimeError('El proveedor no confirmó el envío.')
                    return
                data = json.load(response)
                if not data.get('id'):
                    raise RuntimeError('El proveedor no confirmó el envío.')
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            # Provider responses and credentials must never appear in app messages/logs.
            raise RuntimeError('No se pudo enviar el correo. Inténtalo nuevamente más tarde.') from None
