import io, os, unittest
from datetime import datetime,timedelta
from urllib.error import HTTPError
from unittest.mock import patch
from zoneinfo import ZoneInfo
import demo_notifications as mail
class TransportTests(unittest.TestCase):
    def test_headers_and_ack(self):
        with patch.dict(os.environ,{'PSICOLOGIA_PILOT_RESEND_API_KEY':'fake'}),patch.object(mail,'urlopen',return_value=io.BytesIO(b'{"id":"ack"}')) as call:
            self.assertEqual(mail.send_payload({'from':mail.FROM},'stable-role'),'ack')
        req=call.call_args.args[0]
        self.assertEqual(req.get_header('User-agent'),'pscsantibanez-reservas/1.0')
        self.assertEqual(req.get_header('Accept'),'application/json')
        self.assertEqual(req.get_header('Idempotency-key'),'stable-role')
    def test_safe_error_categories(self):
        for headers,body,expected in [({'Content-Type':'text/html'},b'<h1>Forbidden</h1>','upstream_html_block'),({'Content-Type':'application/json'},b'{"name":"validation_error","message":"private recipient"}','validation_error')]:
            exc=HTTPError(mail.RESEND_ENDPOINT,403,'Forbidden',headers,io.BytesIO(body))
            with patch.dict(os.environ,{'PSICOLOGIA_PILOT_RESEND_API_KEY':'fake'}),patch.object(mail,'urlopen',side_effect=exc):
                with self.assertRaises(mail.NotificationError) as error: mail.send_payload({},'stable-role')
            self.assertEqual(mail.safe_error_label(error.exception),'HTTP_403:'+expected)
            self.assertNotIn('private',str(error.exception))
    def test_fonasa_copy(self):
        start=datetime(2026,10,30,9,tzinfo=ZoneInfo('America/Santiago'))
        with patch.object(mail,'_communication_copy',return_value={}):
            _,html=mail.template('patient','ref','psicoterapia',start,start+timedelta(minutes=45),demonstration=False,meet_url='https://meet.google.com/abc-defg-hij')
        for text in ('Transbank','pago simulado','Adjuntamos'): self.assertNotIn(text,html)
        self.assertIn('meet.google.com/abc-defg-hij',html)
