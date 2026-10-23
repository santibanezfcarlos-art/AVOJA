"""Pruebas sin red de firma del puente y callback Webpay de integración."""
import hmac,hashlib,json,os,unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from flask import Flask
import webpay_demo
import webpay_demo_bridge as bridge

SECRET="a-long-private-signing-key-test-only-123456789123456789"
MAIL="santibanezf.carlos@gmail.com"
ENV={
 "PSICOLOGIA_DEMO_BRIDGE_ENABLED":"true",
 "PSICOLOGIA_DEMO_BRIDGE_KEY":SECRET,
 "PSICOLOGIA_WEBPAY_MODE":"integration",
 "PSICOLOGIA_WEBPAY_COMMERCE_CODE":"597055555532",
 "PSICOLOGIA_WEBPAY_API_KEY":"dummy-integration-key",
 "PSICOLOGIA_WEBPAY_DEMO_SIGNING_KEY":"b"*60,
}
class GatewayStub:
 def __init__(self,response):
  self.response=response
 def __enter__(self):return self
 def __exit__(self,*args):return False
 def read(self,*args):return json.dumps(self.response).encode()

class DemoPublicTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,ENV,clear=True);self.env.start()
  app=Flask(__name__)
  app.register_blueprint(webpay_demo.webpay_demo_bp)
  app.testing=True
  self.client=app.test_client()
  self.date=(datetime.now(ZoneInfo("America/Santiago"))+timedelta(days=4)).date().isoformat()
  self.person={"rut":"17000344-6","name":"Persona de prueba","address":"Calle de prueba 100",
      "email":MAIL,"phone":"+56911111111"}

 def tearDown(self):self.env.stop()
 def form(self):
  return {"service":"psicoterapia","date":self.date,"time":"18:00",
          "billing_required":"1","billing_email":MAIL,"test_email_ownership":"1"}

 def test_bridge_disabled_by_default(self):
  with patch.dict(os.environ,{"PSICOLOGIA_DEMO_BRIDGE_ENABLED":"false"}):
   self.assertFalse(bridge.enabled())

 def test_preflight_signature_does_not_expose_secret_or_browser(self):
  called=[]
  def transport(req,timeout=18):
   called.append(req)
   self.assertEqual(req.get_method(),"POST")
   self.assertEqual(req.full_url,"https://psicologia-retenciones-piloto.onrender.com/internal/psicologia/demo/preflight")
   self.assertNotIn(SECRET,req.data.decode())
   stamp=req.headers.get("X-psc-timestamp")
   signature=req.headers.get("X-psc-signature")
   expected=hmac.new(SECRET.encode(),stamp.encode()+b"."+req.data,hashlib.sha256).hexdigest()
   self.assertEqual(signature,expected)
   return GatewayStub({"state":"READY"})
  with patch("webpay_demo_bridge.urlopen",side_effect=transport):
   self.assertEqual(bridge.preflight(order="PSCD"+"A"*18,session="D2P"+"2"*40,
      service="psicoterapia",day=self.date,hour="18:00",amount=30000,email=MAIL),"READY")
  self.assertEqual(len(called),1)

 def test_webpay_is_not_started_when_preflight_fails(self):
  with patch("webpay_demo.billing.configured",return_value=True),\
       patch("webpay_demo.billing.receiver",return_value=self.person),\
       patch("webpay_demo.bridge.preflight",side_effect=bridge.BridgeError("NOTIFICATIONS_DISABLED")),\
       patch("webpay_demo._send") as bank:
   resp=self.client.post("/api/webpay-demo/start",data=self.form(),
                         headers={"Origin":"https://pscsantibanez.cl"})
   self.assertEqual(resp.status_code,503)
   bank.assert_not_called()

 def test_no_date_or_time_never_calls_bank(self):
  with patch("webpay_demo._send") as bank:
   for day,hour in [("",""),(self.date,""),("","18:00")]:
    form=self.form();form["date"]=day;form["time"]=hour
    result=self.client.post("/api/webpay-demo/start",data=form,
                          headers={"Origin":"https://pscsantibanez.cl"})
    self.assertEqual(result.status_code,400)
   bank.assert_not_called()

 def test_missing_receiver_or_bridge_unavailable_stops_before_bank(self):
  with patch("webpay_demo._send") as bank:
   form=self.form();form.pop("billing_required")
   response=self.client.post("/api/webpay-demo/start",data=form,
                           headers={"Origin":"https://pscsantibanez.cl"})
   self.assertEqual(response.status_code,400)
   with patch.dict(os.environ,{"PSICOLOGIA_DEMO_BRIDGE_ENABLED":"false"}):
    response=self.client.post("/api/webpay-demo/start",data=self.form(),
                             headers={"Origin":"https://pscsantibanez.cl"})
    self.assertEqual(response.status_code,503)
   bank.assert_not_called()

 def test_email_permission_required_before_payment(self):
  with patch("webpay_demo._send") as bank:
   form=self.form();form.pop("test_email_ownership")
   response=self.client.post("/api/webpay-demo/start",data=form,
                             headers={"Origin":"https://pscsantibanez.cl"})
   self.assertEqual(response.status_code,422)
   bank.assert_not_called()

 def test_bank_callback_confirms_via_signed_bridge(self):
  day=self.date
  with patch("webpay_demo.bridge.complete",return_value="CONFIRMED_EMAIL_PENDING") as complete,\
       patch("webpay_demo.billing.record_result",return_value=True),\
       patch("webpay_demo._send") as bank:
   bank.return_value={"session_id":webpay_demo._signed_session("psicoterapia",day,"18:00"),
                      "buy_order":"PSCD"+"F"*18,"amount":30000,
                      "status":"AUTHORIZED","response_code":0}
   res=self.client.post("/api/webpay-demo/return",data={"token_ws":"a"*64})
   self.assertEqual(res.status_code,303)
   receipt=res.location.split("wp_demo=")[1].split("#")[0]
   from urllib.parse import unquote
   data=webpay_demo._receipt_data(unquote(receipt))
   self.assertEqual(data["reservation_status"],"CONFIRMED_EMAIL_PENDING")
   complete.assert_called_once()

if __name__=="__main__":unittest.main()
