import base64,os,sqlite3,unittest
from contextlib import contextmanager
from unittest.mock import patch
from flask import Flask
import fonasa_internal as f
class Tests(unittest.TestCase):
 def setUp(self):
  self.con=sqlite3.connect(':memory:');self.con.row_factory=sqlite3.Row;f._schema(self.con)
  self.con.execute("INSERT INTO fonasa_intake(id,folio,rut,patient_name,emitted,state,created_at,updated_at,calendar_event_id) VALUES ('test','123456','12345678-5','Test','2026-10-26','ready_to_activate','now','now',NULL)")
  self.con.commit();self.app=Flask(__name__);self.app.register_blueprint(f.bp)
  self.env=patch.dict(os.environ,{'PSICOLOGIA_BILLING_ADMIN_PASSWORD':'test-secret','PSICOLOGIA_BILLING_ADMIN_HASH':''});self.env.start()
  self.ready=patch.object(f.billing,'configured',return_value=True);self.ready.start()
  @contextmanager
  def database():
   yield self.con
   self.con.commit()
  self.db=patch.object(f.billing,'db',database);self.db.start()
  self.headers={'Authorization':'Basic '+base64.b64encode(b'carlos:test-secret').decode(),'X-Billing-Action':'manual-invoice'}
 def tearDown(self):self.db.stop();self.ready.stop();self.env.stop();self.con.close()
 def post(self):return self.app.test_client().post('/admin/fonasa/activate/test',json={},headers=self.headers)
 def test_requires_verified_voucher(self):
  self.con.execute("UPDATE fonasa_intake SET state='calendar_pending'")
  with patch.object(f,'_call_worker') as call:self.assertEqual(self.post().status_code,409)
  call.assert_not_called()
 def test_activation_before_calendar_and_running_do_not_resubmit(self):
  with patch.object(f,'_call_worker',return_value={'job_id':'j'*24}) as call:
   self.assertEqual(self.post().get_json()['activation_status'],'running');self.post();call.assert_called_once()
  self.con.execute("UPDATE fonasa_intake SET activation_status='activated'")
  with patch.object(f,'_call_worker') as call:self.assertEqual(self.post().get_json()['activation_status'],'activated')
  call.assert_not_called()
 def test_unknown_result_is_lookup_only(self):
  self.con.execute("UPDATE fonasa_intake SET activation_status='needs_review'")
  with patch.object(f,'_call_worker',return_value={'job_id':'j'*24}) as call:self.post()
  self.assertEqual(call.call_args.args[0],'/bridge/fonasa/start')
 def test_provider_positive_evidence_required(self):
  self.con.execute("UPDATE fonasa_intake SET activation_status='running',activation_job='job'")
  with patch.object(f,'_call_worker',return_value={'state':'found_without_activation_action','activated':False}):
   result=self.app.test_client().get('/admin/fonasa/activation/test',headers=self.headers)
  self.assertEqual(result.get_json()['activation_status'],'needs_review')

 def test_link_requires_activation(self):
  response=self.app.test_client().post('/admin/fonasa/link/test',json={},headers=self.headers)
  self.assertEqual(response.status_code,409)
  self.con.execute("UPDATE fonasa_intake SET activation_status='activated'")
  response=self.app.test_client().post('/admin/fonasa/link/test',json={},headers=self.headers)
  self.assertEqual(response.status_code,200)
  self.assertIn('link',response.get_json())

 def test_duplicate_upload_resumes_existing_record(self):
  import io
  info={'folio':'123456','rut':'12345678-5','patient_name':'Test','emitted':'2026-10-26'}
  with patch.object(f,'parse_tcpdf',return_value=info):
   response=self.app.test_client().post('/admin/fonasa/upload',data={'bono':(io.BytesIO(b'test-only'),'test.pdf')},headers=self.headers)
  self.assertEqual(response.status_code,200,response.get_json())
  self.assertTrue(response.get_json()['existing'])
  self.assertEqual(response.get_json()['bono']['id'],'test')
  self.assertEqual(self.con.execute('SELECT count(*) FROM fonasa_intake').fetchone()[0],1)
