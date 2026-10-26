import sys,types,unittest,os
from unittest.mock import Mock,patch
from flask import Flask,request
provider=Mock()
auth=types.ModuleType('public_demo_bridge');auth._authorize=lambda:request.get_json() if request.headers.get('X-Test-Authorized')=='yes' else None
writer=types.ModuleType('google_calendar_writer');writer.CalendarWriter=lambda:provider
mail=types.ModuleType('demo_notifications');mail.send_payload=Mock();mail.FROM='contacto@pscsantibanez.cl'
with patch.dict(sys.modules,{'public_demo_bridge':auth,'google_calendar_writer':writer,'demo_notifications':mail}):
 import production_fulfillment as p
class FulfillmentTests(unittest.TestCase):
 def setUp(self):
  provider.reset_mock();provider.own_event_exists.return_value=False;provider.is_free.return_value=True;provider.create_paid_appointment.return_value='event';provider.get_meet_link.return_value='https://meet.google.com/abc-defg-hij'
  provider._calendar_path='/cal';provider.settings=types.SimpleNamespace(configured=True,write_enabled=True)
  self.env=patch.dict(os.environ,{'PSICOLOGIA_PRODUCTION_FULFILLMENT_ENABLED':'true','PSICOLOGIA_PILOT_RESEND_API_KEY':'fake','PSICOLOGIA_GOOGLE_MEET_ENABLED':'true'});self.env.start();self.addCleanup(self.env.stop)
  app=Flask(__name__);app.register_blueprint(p.bp);self.client=app.test_client()
  self.data={'channel':'production','order':'PSCP'+'A'*18,'hold_id':'known-uuid','service':'psicoterapia','start':'2026-11-03T10:00:00-03:00','end':'2026-11-03T10:45:00-03:00','name':'Test Persona'}
 def test_public_request_cannot_create_calendar_event(self):
  self.assertEqual(self.client.post('/internal/psicologia/production/confirm',json=self.data).status_code,403);provider.create_paid_appointment.assert_not_called()
 def test_real_confirmation_uses_verified_non_demo_event(self):
  r=self.client.post('/internal/psicologia/production/confirm',json=self.data,headers={'X-Test-Authorized':'yes'})
  self.assertEqual(r.json['state'],'CONFIRMED');self.assertFalse(provider.create_paid_appointment.call_args.kwargs['demo_only']);self.assertTrue(provider.create_paid_appointment.call_args.kwargs['payment_verified'])
 def test_occupied_slot_does_not_create_event(self):
  provider.is_free.return_value=False
  self.assertEqual(self.client.post('/internal/psicologia/production/confirm',json=self.data,headers={'X-Test-Authorized':'yes'}).status_code,409);provider.create_paid_appointment.assert_not_called()
 def test_signed_health_has_no_calendar_mutation(self):
  self.assertEqual(self.client.post('/internal/psicologia/production/health',json={'channel':'production'},headers={'X-Test-Authorized':'yes'}).json['state'],'READY');provider.create_paid_appointment.assert_not_called()
if __name__=='__main__':unittest.main()
