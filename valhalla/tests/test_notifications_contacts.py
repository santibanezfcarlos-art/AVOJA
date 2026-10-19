import base64
import json
import os
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch,MagicMock
from app import Store,Problem,ADMIN_EMAIL,CHILE,stamp
from mailer import EmailDelivery,EmailDeliveryError
from notifications import NotificationDispatcher,notification_message

class FakeNotifications:
    configured=True
    provider='resend'
    def __init__(self):self.calls=[];self.fail=False
    def send_notification(self,email,kind,payload,key):
        self.calls.append((email,kind,payload,key))
        if self.fail:raise EmailDeliveryError('network_error')

class NotificationsAndContacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'data.sqlite3')
        self.clock=datetime(2026,10,19,8,tzinfo=CHILE)
        self.clock_patch=patch('app.now',return_value=self.clock);self.clock_patch.start();self.addCleanup(self.clock_patch.stop)
        self.store.configure_access('AdministratorTest2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'AdministratorTest2026!'}))['id']
        self.credentials={'name':'Juanito Prueba','email':'juanito@example.cl','password':'ProfessionalTest2026!','specialty':'Psicología'}
        self.uid=self.professional(self.credentials)
        self.delivery=FakeNotifications();self.dispatcher=NotificationDispatcher(self.store,self.delivery)
    def professional(self,credentials):
        uid=self.store.register(credentials);token,_=self.store.issue_email_token(uid);self.store.confirm_email(token)
        self.store.import_legacy(self.admin,{'name':credentials['name'],'email':credentials['email'],'specialty':credentials['specialty'],'reference':'old-'+credentials['email'],'sessions':10,'note':'Identidad y saldo verificados','identity_confirmed':True})
        return uid
    def slot(self,day=22,hour=10,box=1):return {'box':box,'start':stamp(datetime(2026,10,day,hour,tzinfo=CHILE))}
    def queue(self,kind=None):
        with self.store.connection() as c:
            return [dict(row) for row in c.execute('SELECT * FROM notification_outbox ORDER BY id') if kind is None or row['kind']==kind]
    def booking_only(self):
        with self.store.connection(True) as c:c.execute("DELETE FROM notification_outbox WHERE kind='admin_signup'")
        return self.store.reserve(self.uid,self.slot())

    def test_signup_notifies_only_administrator_and_never_contains_password(self):
        job=self.queue('admin_signup')[0]
        self.assertEqual(job['recipient'],ADMIN_EMAIL)
        self.assertEqual(json.loads(job['payload'])['email'],self.credentials['email'])
        self.assertNotIn(self.credentials['password'],job['payload'])
        with self.assertRaises(Problem):self.store.register(self.credentials)
        self.assertEqual(len(self.queue('admin_signup')),1)

    def test_document_notice_has_count_not_content_or_private_filename(self):
        self.store.upload(self.uid,{'kind':'identity','files':[{'filename':'private-identity.heic','content':base64.b64encode(b'private identity bytes').decode()},{'filename':'reverse.jpg','content':base64.b64encode(b'reverse private bytes').decode()}]})
        job=self.queue('admin_document')[0];payload=json.loads(job['payload'])
        self.assertEqual(job['recipient'],ADMIN_EMAIL);self.assertEqual(payload['file_count'],2)
        subject,text=notification_message(job['kind'],payload,'https://valhallaoffice.cl')
        self.assertIn('Documentación recibida',subject)
        self.assertNotIn('private-identity',text);self.assertNotIn('private identity bytes',job['payload'])
        with self.assertRaises(Problem):self.store.upload(self.uid,{'files':[{'filename':'x','content':'invalid!!'}]})
        self.assertEqual(len(self.queue('admin_document')),1)

    def test_receipt_is_atomic_with_reservation_and_duplicate_booking_does_not_queue(self):
        bid=self.booking_only();job=self.queue('booking_confirmed')[0]
        self.assertEqual(job['recipient'],self.credentials['email'])
        subject,text=notification_message(job['kind'],json.loads(job['payload']),'https://valhallaoffice.cl')
        for value in ('Reserva confirmada','Box 1','22/10/2026','10:00 a 10:55','America/Santiago',f'#{bid}'):
            self.assertIn(value,subject+' '+text)
        with self.assertRaises(Problem):self.store.reserve(self.uid,self.slot())
        self.assertEqual(len(self.queue('booking_confirmed')),1)
        self.assertNotIn('notification_outbox',str(self.store.state(self.store.session(self.store.login(self.credentials)))))

    def test_failed_conflict_never_queues_receipt_for_losing_client(self):
        self.store.reserve(self.uid,self.slot())
        other=self.professional(self.credentials|{'name':'Pedrito Prueba','email':'pedrito@example.cl'})
        with self.assertRaises(Problem):self.store.reserve(other,self.slot())
        self.assertEqual(len(self.queue('booking_confirmed')),1)

    def test_failed_email_preserves_reservation_and_retry_uses_same_key(self):
        bid=self.booking_only();self.delivery.fail=True
        self.dispatcher.dispatch_pending()
        job=self.queue()[0];self.assertEqual(job['status'],'retry')
        with self.store.connection() as c:
            self.assertEqual(c.execute('SELECT status FROM bookings WHERE id=?',(bid,)).fetchone()[0],'confirmed')
            self.assertEqual(c.execute('SELECT remaining FROM packages WHERE user_id=?',(self.uid,)).fetchone()[0],9)
        self.assertEqual(self.dispatcher.dispatch_pending(),0)
        with self.store.connection(True) as c:c.execute('UPDATE notification_outbox SET next_attempt=0')
        self.delivery.fail=False;self.dispatcher.dispatch_pending();self.dispatcher.dispatch_pending()
        self.assertEqual(len(self.delivery.calls),2);self.assertEqual(self.delivery.calls[0][3],self.delivery.calls[1][3])
        self.assertEqual(self.queue()[0]['status'],'accepted')

    def test_retry_exhaustion_and_unconfigured_provider_do_not_claim_success(self):
        self.booking_only();self.delivery.configured=False
        self.assertEqual(self.dispatcher.dispatch_pending(),0);self.assertEqual(self.queue()[0]['attempts'],0)
        self.delivery.configured=True;self.delivery.fail=True
        for _ in range(5):
            with self.store.connection(True) as c:c.execute('UPDATE notification_outbox SET next_attempt=0')
            self.dispatcher.dispatch_pending()
        self.assertEqual(self.queue()[0]['status'],'failed');self.assertEqual(len(self.delivery.calls),5)
        self.assertEqual(self.dispatcher.dispatch_pending(),0)

    def test_modification_and_cancellation_supersede_old_unsent_receipts(self):
        bid=self.booking_only();self.store.modify(self.uid,bid,self.slot(hour=11,box=2))
        self.assertEqual(self.queue('booking_confirmed')[0]['status'],'superseded')
        self.store.admin_action(self.admin,{'action':'cancel','id':bid})
        self.assertEqual(self.queue('booking_modified')[0]['status'],'superseded')
        self.dispatcher.dispatch_pending(limit=5)
        self.assertEqual([call[1] for call in self.delivery.calls],['booking_cancelled'])
        _,text=notification_message('booking_cancelled',self.delivery.calls[0][2],'https://valhallaoffice.cl')
        self.assertIn('11:00 a 11:55',text);self.assertIn('Box 2',text)
        with self.assertRaises(Problem):self.store.admin_action(self.admin,{'action':'cancel','id':bid})
        self.assertEqual(len(self.queue('booking_cancelled')),1)

    def test_expired_delivery_lease_is_recovered_with_original_key(self):
        self.booking_only();job=self.dispatcher.claim()
        self.assertIsNotNone(job);self.assertIsNone(self.dispatcher.claim())
        with self.store.connection(True) as c:c.execute('UPDATE notification_outbox SET lease_until=0')
        self.dispatcher.dispatch_pending();self.assertEqual(self.delivery.calls[0][3],job['delivery_key'])
        self.assertEqual(self.queue()[0]['status'],'accepted')

    def test_uncertain_delivery_is_not_retried_outside_provider_idempotency_window(self):
        import time
        self.booking_only()
        with self.store.connection(True) as c:
            c.execute("UPDATE notification_outbox SET status='retry',attempts=1,first_attempt=?,next_attempt=0",(time.time()-86400,))
        self.assertEqual(self.dispatcher.dispatch_pending(),0)
        self.assertEqual(self.queue()[0]['status'],'failed')
        self.assertEqual(self.delivery.calls,[])

    def test_daily_consolidation_is_unique_and_excludes_cancelled_or_other_days(self):
        second=self.professional(self.credentials|{'name':'Pedrito Prueba','email':'pedrito@example.cl'})
        third=self.professional(self.credentials|{'name':'Verónica Prueba','email':'veronica@example.cl'})
        self.store.reserve(self.uid,self.slot());self.store.reserve(self.uid,self.slot(hour=11))
        self.store.reserve(second,self.slot(box=2));self.store.reserve(third,self.slot(box=3))
        cancelled=self.store.reserve(third,self.slot(hour=12));self.store.admin_action(self.admin,{'action':'cancel','id':cancelled})
        self.store.reserve(third,self.slot(day=23,hour=12))
        report=self.store.daily_contacts('2026-10-22')
        self.assertEqual((report['total_professionals'],report['total_bookings']),(3,4))
        self.assertEqual(set(report['emails']),{'juanito@example.cl','pedrito@example.cl','veronica@example.cl'})
        self.assertEqual(len(report['emails']),3)
        self.assertEqual(len(next(p for p in report['contacts'] if p['user_id']==self.uid)['bookings']),2)
        self.assertEqual(self.store.daily_contacts('2026-10-21')['emails'],[])
        with self.assertRaises(Problem):self.store.daily_contacts('not-a-date')

    def test_day_is_selected_in_chile_instead_of_utc(self):
        # 01:00 UTC on the 23rd is 22:00 in Chile on the 22nd. Insert only for this boundary test.
        with self.store.connection(True) as c:
            pid=c.execute('SELECT id FROM packages WHERE user_id=?',(self.uid,)).fetchone()[0]
            c.execute("INSERT INTO bookings(user_id,package_id,box,start,created_at) VALUES(?,?,1,'2026-10-23T01:00:00+00:00',?)",(self.uid,pid,stamp(self.clock)))
        self.assertEqual(self.store.daily_contacts('2026-10-22')['total_bookings'],1)
        self.assertEqual(self.store.daily_contacts('2026-10-23')['total_bookings'],0)

    def test_modified_booking_moves_between_daily_reports(self):
        bid=self.booking_only();self.store.modify(self.uid,bid,self.slot(day=23,hour=11))
        self.assertEqual(self.store.daily_contacts('2026-10-22')['total_bookings'],0)
        self.assertEqual(self.store.daily_contacts('2026-10-23')['total_bookings'],1)

class ResendNotificationProtocol(unittest.TestCase):
    def test_retry_key_is_attached_to_resend_request(self):
        with patch.dict(os.environ,{'VALHALLA_EMAIL_PROVIDER':'resend','VALHALLA_RESEND_API_KEY':'test-only','VALHALLA_EMAIL_FROM':'notificaciones@valhallaoffice.cl'},clear=True):
            delivery=EmailDelivery();response=MagicMock();response.__enter__.return_value.read.return_value=b'{"id":"test-message"}'
            with patch('mailer.urlopen',return_value=response) as send:
                delivery.send_notification('owner@example.cl','admin_signup',{'name':'Prueba','email':'new@example.cl','specialty':'Psicología'},'stable-key')
                request=send.call_args.args[0];self.assertEqual(request.get_header('Idempotency-key'),'valhalla-stable-key')
                self.assertEqual(json.loads(request.data)['to'],['owner@example.cl'])
