import os, tempfile, unittest, copy
from unittest.mock import patch
from datetime import date,timedelta
import main, admin, billing, webpay_demo

class AdminTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.env=patch.dict(os.environ,{'RENDER':'','PSICOLOGIA_BILLING_DB':self.tmp.name+'/db.sqlite3','PSICOLOGIA_BILLING_ADMIN_PASSWORD':'offline-testing-password','PSICOLOGIA_BILLING_ADMIN_HASH':'','PSICOLOGIA_DEMO_BRIDGE_ENABLED':'false'})
  self.env.start();self.client=main.app.test_client();self.auth=('carlos','offline-testing-password')
 def tearDown(self):self.env.stop();self.tmp.cleanup()
 def post(self,path,body):return self.client.post(path,json=body,auth=self.auth,headers={'X-Billing-Action':'manual-invoice'})
 def test_private_routes(self):
  for path in ['/admin','/admin/settings','/admin/transbank','/admin/assets/admin.js']:
   self.assertEqual(self.client.get(path).status_code,401)
   r=self.client.get(path,auth=self.auth,headers={'Origin':'https://pscsantibanez.cl'})
   self.assertEqual(r.status_code,200);self.assertNotIn('Access-Control-Allow-Origin',r.headers);r.close()
 def test_save_shared_and_conflict(self):
  d=self.client.get('/admin/settings',auth=self.auth).json
  d['settings']['weekly']['1']=['10:00'];d['settings']['exceptions']['2026-12-01']={'closed':True,'slots':[]}
  self.assertEqual(self.post('/admin/settings',d).status_code,200)
  self.assertEqual(self.post('/admin/settings',d).status_code,409)
  saved=self.client.get('/api/booking-config').json['settings']
  self.assertEqual(saved['weekly']['1'],['10:00']);self.assertFalse(admin.allowed_slot(saved,'psicoterapia','2026-12-01','10:00'))
 def test_validation(self):
  for field,value in [('maxPerDay',4),('weekly',{}),('services',{}),('exceptions',{'bad':{'closed':True,'slots':[]}})]:
   data=copy.deepcopy(admin.DEFAULT);data[field]=value
   self.assertEqual(self.post('/admin/settings',{'settings':data,'version':0}).status_code,400)
 def test_prices_and_duration_are_editable_and_persisted(self):
  data=copy.deepcopy(admin.DEFAULT)
  data['services']['psicoterapia']['price']=35000
  data['services']['psicoterapia']['minutes']=60
  result=self.post('/admin/settings',{'settings':data,'version':0})
  self.assertEqual(result.status_code,200,result.get_data(as_text=True))
  public=self.client.get('/api/booking-config').json['settings']
  self.assertEqual(public['services']['psicoterapia']['price'],35000)
  self.assertEqual(public['services']['psicoterapia']['minutes'],60)

 def test_csrf(self):
  self.assertEqual(self.client.post('/admin/settings',auth=self.auth,json={}).status_code,403)
 def test_transbank_secret_and_pause(self):
  with patch.dict(os.environ,{'PSICOLOGIA_WEBPAY_API_KEY':'never-expose-test-secret','PSICOLOGIA_WEBPAY_MODE':'integration','PSICOLOGIA_WEBPAY_COMMERCE_CODE':webpay_demo.TEST_COMMERCE,webpay_demo.SIGNING_KEY_VAR:'x'*50}):
   r=self.post('/admin/transbank',{'paused':True});self.assertTrue(r.json['paused']);self.assertNotIn('never-expose-test-secret',r.get_data(as_text=True))
   r=self.client.post('/api/webpay-demo/start',headers={'Origin':webpay_demo.SITE},data={'service':'psicoterapia'})
   self.assertEqual(r.status_code,503);self.assertIn('pausados',r.get_data(as_text=True))
 def test_bridge_keeps_configurable_price(self):
  with patch('webpay_demo_bridge.enabled',return_value=True):
   d=copy.deepcopy(admin.DEFAULT);d['services']['psicoterapia']['price']=1000
   self.assertEqual(self.post('/admin/settings',{'settings':d,'version':0}).status_code,200)
   self.assertEqual(self.client.get('/api/booking-config').json['settings']['services']['psicoterapia']['price'],1000)
if __name__=='__main__':unittest.main()
