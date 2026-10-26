import base64,os,unittest
from contextlib import contextmanager
from datetime import datetime,timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo
from icalendar import Calendar
from flask import Flask
import fonasa_rebooking as adapter
import rebooking_preview as preview
from fonasa_mail import calendar_attachment,send
from patient_names import display_name
TZ=ZoneInfo('America/Santiago')
class FonasaTests(unittest.TestCase):
 def test_display_names(self):
  self.assertEqual(display_name('NADIA CAROLINA ARAVENA PIZARRO'),'Nadia Carolina Aravena Pizarro')
  self.assertEqual(display_name('MARÍA DE LA LUZ'),'María de la Luz')
  self.assertEqual(display_name('Ana McDonald'),'Ana McDonald')
 def test_corporate_calendar(self):
  start=datetime(2026,11,6,9,tzinfo=TZ)
  doc=calendar_attachment({'id':'event','iCalUID':'stable@google.com'},start,start+timedelta(minutes=45))
  cal=Calendar.from_ical(base64.b64decode(doc['content']));event=cal.walk('VEVENT')[0]
  self.assertEqual(str(cal['method']),'PUBLISH')
  self.assertEqual(str(event['organizer']),'mailto:contacto@pscsantibanez.cl')
  self.assertEqual(str(event['status']),'CONFIRMED')
  self.assertEqual(str(event['uid']),'stable@google.com')
  self.assertEqual(event.decoded('dtstart'),start)
 def test_one_use_and_cutoff(self):
  now=datetime(2026,10,26,10,tzinfo=TZ)
  row={'state':'CONFIRMED','redeemed_at':None,'starts_at':now+timedelta(hours=24)}
  self.assertEqual(adapter.eligible(row,now=now)[1],'eligible')
  self.assertEqual(adapter.eligible(row,now=now+timedelta(seconds=1))[1],'too_late')
  row['redeemed_at']=now
  self.assertEqual(adapter.eligible(row,now=now)[1],'used')
 def test_mail_has_shared_button(self):
  start=datetime(2026,11,6,9,tzinfo=TZ)
  link='https://psicologia-retenciones-piloto.onrender.com/reagendar?t='+'a'*64
  with patch('fonasa_mail.send_payload',return_value='ack') as transport,patch('demo_notifications._communication_copy',return_value={}):
   send('patient','ref','test@example.org','NADIA ARAVENA',start,start+timedelta(minutes=45),{'id':'event'},rebooking_url=link)
  payload=transport.call_args.args[0]
  self.assertIn('Reagendar mi atención',payload['html']);self.assertIn(link,payload['html'])
  self.assertNotIn('Transbank',payload['html']);self.assertEqual(len(payload['attachments']),1)
 def test_shared_confirm_engine(self):
  old=datetime.now(TZ).replace(hour=9,minute=0,second=0,microsecond=0)+timedelta(days=8);new=old+timedelta(days=1)
  row={'source':'fonasa','order_id':'source-reference','hold_id':'hold-id','service':'psicoterapia','slot_date':old.date(),'starts_at':old,'ends_at':old+timedelta(minutes=45),'state':'CONFIRMED','test_recipient':'test@example.org','patient_name':'Test'}
  class Cursor:
   def fetchone(self):return None
   def fetchall(self):return []
  class Conn:
   def __init__(self):self.queries=[];self.committed=False
   def execute(self,sql,args=None):self.queries.append(sql);return Cursor()
   def commit(self):self.committed=True
   def rollback(self):pass
  conn=Conn()
  @contextmanager
  def database():yield conn
  app=Flask(__name__);app.register_blueprint(preview.rebooking_bp)
  with patch.dict(os.environ,{'PSICOLOGIA_REBOOKING_PREVIEW_ENABLED':'true','PSICOLOGIA_REBOOKING_CONFIRM_ENABLED':'true'}),patch.object(preview,'_database_connection',database),patch('fonasa_rebooking.lookup',return_value=row),patch.object(preview,'_booking',return_value=row),patch.object(preview,'check',return_value=(row,'eligible')),patch.object(preview,'slot_window',return_value=(new.date(),new,new+timedelta(minutes=45))),patch.object(preview,'configured_slots',return_value=({'09:00'},3)),patch('booking_holds._lock_day'),patch('booking_holds._expire'),patch('google_calendar_writer.CalendarWriter') as writer,patch('fonasa_rebooking.redeem') as redeem,patch('fonasa_calendar_fulfillment.notify_rebooked_fonasa') as notify:
   writer.return_value.is_free.return_value=True
   response=app.test_client().post('/api/reagendar/'+'a'*64+'/confirmar',json={'date':new.date().isoformat(),'time':'09:00'})
  self.assertEqual(response.status_code,200,response.get_json());self.assertTrue(conn.committed)
  redeem.assert_called_once();notify.assert_called_once()
  self.assertFalse(any('INSERT INTO psychology_demo_payments' in q for q in conn.queries))
  writer.return_value.reschedule_own_appointment.assert_called_once()

class PreparationTests(unittest.TestCase):
 def test_prepare_does_not_send(self):
  import fonasa_calendar_fulfillment as f
  body={'intake_id':'a'*20,'role':'patient','email':'test@example.org','patient_name':'NADIA ARAVENA','date':'2026-10-30','time':'09:00','minutes':45,'prepare_only':True,'refresh_presentation':True}
  app=Flask(__name__);app.register_blueprint(f.bp)
  @contextmanager
  def database():yield object()
  with patch.dict(os.environ,{'PSICOLOGIA_PILOT_RESEND_API_KEY':'x'*40,'PSICOLOGIA_PILOT_NOTIFICATIONS_ENABLED':'true'}),patch.object(f,'_authorize',return_value=body),patch.object(f,'_enabled',return_value=True),patch.object(f,'_owns',return_value=True),patch.object(f,'CalendarWriter'),patch('booking_holds._database_connection',database),patch('fonasa_rebooking.register',return_value='a'*64),patch('fonasa_mail.send') as send:
   response=app.test_client().post('/internal/psicologia/fonasa/rebooking-prepare',json=body)
  self.assertEqual(response.status_code,200,response.get_json());self.assertEqual(response.get_json()['state'],'PREPARED');send.assert_not_called()
