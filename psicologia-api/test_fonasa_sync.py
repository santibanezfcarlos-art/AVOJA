import hashlib,hmac,json,os,time,unittest
from unittest.mock import MagicMock,patch
from flask import Flask
import fonasa_internal as f
class SyncTests(unittest.TestCase):
 def setUp(self):
  self.app=Flask(__name__);self.app.register_blueprint(f.bp)
  self.key='k'*64
  self.row={'state':'calendar_confirmed','calendar_event_id':'own-event','requested_date':'2026-10-30','requested_time':'09:00','id':'own-intake'}
  self.data={'intake_id':'own-intake','event_id':'own-event','old_start':'2026-10-30T09:00:00-03:00','new_start':'2026-11-02T11:00:00-03:00'}
 def request(self,**changes):
  data={**self.data,**changes};raw=json.dumps(data).encode();stamp=str(int(time.time()))
  signature=hmac.new(self.key.encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
  db=MagicMock();db.__enter__.return_value=db
  with patch.dict(os.environ,{'PSICOLOGIA_DEMO_BRIDGE_KEY':self.key}),patch.object(f.billing,'db',return_value=db),patch.object(f,'_record',return_value=self.row):
   result=self.app.test_client().post('/internal/fonasa/rescheduled',data=raw,content_type='application/json',headers={'X-Psc-Timestamp':stamp,'X-Psc-Signature':signature})
  return result,db
 def test_private(self):
  self.assertEqual(self.app.test_client().post('/internal/fonasa/rescheduled',json={}).status_code,403)
 def test_only_owned_booking(self):
  response,db=self.request(event_id='different-event');self.assertEqual(response.status_code,409);db.execute.assert_not_called()
 def test_update_and_idempotency(self):
  response,db=self.request();self.assertEqual(response.status_code,200);db.execute.assert_called_once()
  self.row.update(requested_date='2026-11-02',requested_time='11:00')
  response,db=self.request();self.assertEqual(response.status_code,200);db.execute.assert_not_called()
 def test_stale_and_naive_rejected(self):
  response,db=self.request(old_start='2026-10-29T09:00:00-03:00');self.assertEqual(response.status_code,409);db.execute.assert_not_called()
  response,db=self.request(new_start='2026-11-02T11:00:00');self.assertEqual(response.status_code,400);db.execute.assert_not_called()
