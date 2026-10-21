import json
import os
import unittest
from html.parser import HTMLParser
from unittest.mock import patch,MagicMock
from notifications import notification_html,notification_message,booking_links
from mailer import EmailDelivery

class ParsedEmail(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.text=[];self.items=0;self.images=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='a':self.links.append(attrs['href'])
        if tag=='li':self.items+=1
        if tag=='img':self.images.append(attrs)
    def handle_data(self,data):self.text.append(data)

class BookingEmailDesign(unittest.TestCase):
    def setUp(self):
        self.payload={'name':'Profesional de prueba','email':'pro@example.cl','booking':{'id':1,'box':1,'start':'2026-10-21T10:00:00+00:00'},'remaining':0}
        self.url='https://valhallaoffice.cl'
    def test_ordered_buttons_bullets_details_and_no_visible_long_urls(self):
        html=notification_html('booking_confirmed',self.payload,self.url);parsed=ParsedEmail();parsed.feed(html)
        links=booking_links(self.payload['booking'],self.url)
        self.assertEqual(parsed.links,[self.url+'/',links['bookings'],links['calendar'],links['directions']])
        self.assertEqual(parsed.items,6)
        text=' '.join(parsed.text)
        for item in ('¡Tu espacio te espera!','Miércoles 21 de octubre de 2026','07:00–07:55','Box 1','Colo Colo 222, oficina 710','0 sesiones','Mis reservas','Agregar al calendario','Cómo llegar'):
            self.assertIn(item,text)
        self.assertNotIn('https://',text)
        self.assertLess(html.index('Ten presente'),html.index('>Mis reservas</a>'))
    def test_customer_text_is_escaped_and_real_logo_uses_absolute_url(self):
        with patch('notifications.Path.is_file',return_value=True):
            html=notification_html('booking_confirmed',self.payload|{'name':'<script>alert("x")</script>'},self.url)
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        parsed=ParsedEmail();parsed.feed(html)
        self.assertEqual(parsed.images[0]['src'],self.url+'/images/valhalla-logo.png')
        self.assertEqual(parsed.images[0]['alt'],'Valhalla')
    def test_modified_and_cancelled_states_are_accurate(self):
        modified=notification_html('booking_modified',self.payload|{'remaining':1},self.url)
        self.assertIn('Tu reserva fue actualizada',modified);self.assertIn('1 sesión.',modified)
        self.assertIn('actualízalo también',modified)
        cancelled=ParsedEmail();cancelled.feed(notification_html('booking_cancelled',self.payload,self.url))
        self.assertEqual(cancelled.links,[self.url+'/',self.url+'/#mis-reservas'])
        self.assertIn('Tu reserva fue cancelada',' '.join(cancelled.text))
        self.assertIsNone(notification_html('admin_signup',{},self.url))
    def test_plain_text_fallback_is_bulleted(self):
        _,text=notification_message('booking_confirmed',self.payload,self.url)
        self.assertEqual(len([line for line in text.splitlines() if line.startswith('• ')]),6)
        self.assertIn('• Saldo disponible al confirmar: 0 sesiones.',text)
    def test_both_providers_send_html_and_plaintext_with_same_links(self):
        for provider in ('resend','sendgrid'):
            with self.subTest(provider=provider),patch.dict(os.environ,{'VALHALLA_EMAIL_PROVIDER':provider,'VALHALLA_RESEND_API_KEY':'test-only','VALHALLA_SENDGRID_API_KEY':'test-only','VALHALLA_EMAIL_FROM':'Valhalla <notificaciones@valhallaoffice.cl>','VALHALLA_PUBLIC_URL':self.url},clear=True):
                response=MagicMock();response.__enter__.return_value.status=202;response.__enter__.return_value.read.return_value=b'{"id":"test-only"}'
                with patch('mailer.urlopen',return_value=response) as send:
                    EmailDelivery().send_notification('pro@example.cl','booking_confirmed',self.payload,'test-delivery')
                body=json.loads(send.call_args.args[0].data)
                if provider=='resend':
                    self.assertIn('html',body);self.assertIn('text',body)
                    self.assertEqual(send.call_args.args[0].get_header('Idempotency-key'),'valhalla-test-delivery')
                else:
                    self.assertEqual([part['type'] for part in body['content']],['text/plain','text/html'])
                self.assertIn('Cómo llegar',json.dumps(body,ensure_ascii=False))
