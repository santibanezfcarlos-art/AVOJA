import os,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime,timedelta
from flask import Flask
import billing
import production_checkout as p

class FakeGateway:
    def __init__(self):
        self.config=SimpleNamespace(commerce_code='597053107390',host='webpay3g.transbank.cl')
        self.creates=0;self.commits=0;self.order=None;self.bad=False
    def _request(self,method,data):
        self.creates+=1;self.order=data
        return {'token':'a'*64,'url':'https://webpay3g.transbank.cl/webpayserver/initTransaction'}
    def commit(self,token):self.commits+=1;return self.status(token)
    def status(self,token):
        return {'buy_order':self.order['buy_order'],'session_id':self.order['session_id'],'amount':1 if self.bad else self.order['amount'],'status':'AUTHORIZED','response_code':0}

class ProductionCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=patch.dict(os.environ,{'PSICOLOGIA_BILLING_DB':self.temp.name+'/billing.db','PSICOLOGIA_BILLING_ADMIN_PASSWORD':'offline-admin-password','PSICOLOGIA_PRODUCTION_SALES_ENABLED':'true'},clear=True);self.env.start();self.addCleanup(self.env.stop)
        self.g=FakeGateway();self.gpatch=patch.object(p,'gateway',return_value=self.g);self.gpatch.start();self.addCleanup(self.gpatch.stop)
        self.remote=patch.object(p,'remote',side_effect=lambda action,data:{'state':'CONFIRMED','event_id':'own-event','meet_url':'https://meet.google.com/abc-defg-hij'} if action=='confirm' else {'state':'READY' if action=='health' else 'SENT'});self.calls=self.remote.start();self.addCleanup(self.remote.stop)
        self.busy=patch('webpay_demo_bridge.busy_intervals',return_value=[]);self.busy.start();self.addCleanup(self.busy.stop)
        app=Flask(__name__);app.register_blueprint(p.bp);self.client=app.test_client()
        p.init()
        with billing.db() as c:c.execute("INSERT INTO webpay_validation(id,commerce_code,order_id,session_id,state,created_at) VALUES(1,?,'validation','session','approved',?)",(self.g.config.commerce_code,billing.now()))
        day=datetime.now(p.TZ).date()+timedelta(days=7)
        while day.weekday()>4:day+=timedelta(days=1)
        self.data={'service':'psicoterapia','date':day.isoformat(),'time':'10:00','booking_policy_accepted':'1','payment_request_id':'test-request-key-123456','billing_rut':'11111111-1','billing_name':'Test Persona','billing_address':'Calle Test 123','billing_email':'test@example.com','billing_phone':'+56912345678','amount':'1'}
    def start(self):return self.client.post('/api/webpay/start',data=self.data,headers={'Origin':p.SITE})
    def test_approved_payment_dispatches_automatically_once(self):
        with patch('sii_canary.start_emission', return_value=({},202)) as emit:
            self.start()
            for _ in range(2):
                self.client.get('/api/webpay/return?token_ws='+'a'*64)
            emit.assert_called_once_with(self.g.order['buy_order'], 'webpay-approved-auto')

    def test_automatic_dispatch_uses_durable_coordinator(self):
        with (patch.dict(os.environ, {'SII_CANARY_ENABLED':'true',
                'SII_VISUAL_BROWSER_URL':'https://psc-sii-visual-test.onrender.com',
                'SII_VISUAL_TICKET_SECRET':'offline-secret-long-enough-for-tests-12345'}),
              patch('sii_dispatch.threading.Thread') as thread):
            self.start()
            for _ in range(2):
                self.client.get('/api/webpay/return?token_ws='+'a'*64)
            self.assertEqual(thread.call_count, 1)
            thread.return_value.start.assert_called_once()
            with billing.db() as c:
                self.assertEqual(c.execute('SELECT status FROM sii_issuance_queue').fetchone()[0], 'processing')
                self.assertEqual(c.execute('SELECT actor FROM billing_audit WHERE action=?',
                    ('render_sii_dispatch_queued',)).fetchone()[0], 'webpay-approved-auto')

    def test_rejected_payment_does_not_dispatch(self):
        self.start()
        rejected=self.g.status('a'*64)
        rejected.update(status='FAILED', response_code=-1)
        with (patch.object(self.g,'commit',return_value=rejected),
              patch('sii_canary.start_emission') as emit):
            self.client.get('/api/webpay/return?token_ws='+'a'*64)
            emit.assert_not_called()

    def test_mismatched_payment_does_not_dispatch(self):
        with patch('sii_canary.start_emission') as emit:
            self.start();self.g.bad=True
            self.client.get('/api/webpay/return?token_ws='+'a'*64)
            emit.assert_not_called()

    def test_emission_failure_preserves_approved_payment_and_reservation(self):
        with patch('sii_canary.start_emission', side_effect=RuntimeError('offline-test')):
            self.start()
            self.assertEqual(self.client.get('/api/webpay/return?token_ws='+'a'*64).status_code,303)
        with billing.db() as c:
            self.assertEqual(c.execute('SELECT state FROM production_payments').fetchone()[0], 'CONFIRMED')
            self.assertEqual(c.execute('SELECT payment_status FROM billing_orders').fetchone()[0], 'approved')

    def test_server_price_and_repeated_submission_do_not_create_second_charge(self):
        self.assertEqual(self.start().status_code,303);self.assertEqual(self.g.order['amount'],30000)
        self.assertEqual(self.start().status_code,303);self.assertEqual(self.g.creates,1)
    def test_approved_return_confirms_once_and_keeps_real_billing(self):
        self.start();self.assertEqual(self.client.get('/api/webpay/return?token_ws='+'a'*64).status_code,303)
        self.client.get('/api/webpay/return?token_ws='+'a'*64)
        self.assertEqual(self.g.commits,1)
        with billing.db() as c:
            row=c.execute('SELECT * FROM billing_orders').fetchone();self.assertEqual((row['payment_status'],row['environment'],row['invoice_status']),('approved','production','pending'))
            row=c.execute('SELECT * FROM production_payments').fetchone();self.assertEqual((row['state'],row['patient_mail'],row['professional_mail']),('CONFIRMED',1,1))
        self.assertEqual(len([x for x in self.calls.call_args_list if x.args[0]=='confirm']),1)
    def test_mismatched_amount_never_fulfills(self):
        self.start();self.g.bad=True;self.assertEqual(self.client.get('/api/webpay/return?token_ws='+'a'*64).status_code,503)
        self.assertFalse(any(x.args[0] in ('confirm','mail') for x in self.calls.call_args_list))
        with billing.db() as c:self.assertEqual(c.execute('SELECT state FROM production_payments').fetchone()['state'],'RECONCILIATION')
    def test_no_validation_blocks_bank_creation(self):
        with billing.db() as c:c.execute('DELETE FROM webpay_validation')
        self.assertEqual(self.start().status_code,503);self.assertEqual(self.g.creates,0)
    def test_competing_hold_blocks_second_attempt(self):
        self.start();self.data['payment_request_id']='another-request-12345678'
        self.assertEqual(self.start().status_code,409);self.assertEqual(self.g.creates,1)
    def test_calendar_failure_keeps_paid_order_for_retry(self):
        self.start();self.calls.side_effect=RuntimeError('offline')
        self.client.get('/api/webpay/return?token_ws='+'a'*64)
        with billing.db() as c:
            self.assertEqual(c.execute('SELECT payment_status FROM billing_orders').fetchone()['payment_status'],'approved')
            self.assertEqual(c.execute('SELECT state FROM production_payments').fetchone()['state'],'AUTHORIZED')

    def test_mail_retry_does_not_create_another_calendar_event(self):
        self.start()
        def first(action,data):
            if action=='confirm':return {'state':'CONFIRMED','event_id':'own-event','meet_url':'https://meet.google.com/abc-defg-hij'}
            raise RuntimeError('mail unavailable')
        self.calls.side_effect=first
        response=self.client.get('/api/webpay/return?token_ws='+'a'*64)
        with billing.db() as c:
            row=c.execute('SELECT * FROM production_payments').fetchone();self.assertEqual(row['state'],'CONFIRMED');self.assertEqual(row['patient_mail'],0)
        self.calls.side_effect=lambda action,data:{'state':'SENT'}
        self.client.get(response.location)
        with billing.db() as c:
            row=c.execute('SELECT * FROM production_payments').fetchone();self.assertEqual((row['patient_mail'],row['professional_mail']),(1,1))
        self.assertEqual(len([x for x in self.calls.call_args_list if x.args[0]=='confirm']),1)
    def test_one_reprogramming_updates_ledger_and_rejects_another(self):
        self.start();self.client.get('/api/webpay/return?token_ws='+'a'*64)
        with billing.db() as c:row=c.execute('SELECT * FROM production_payments').fetchone()
        next_day=(datetime.fromisoformat(row['starts_at'])+timedelta(days=7)).date().isoformat()
        self.calls.side_effect=lambda action,data:{'state':'CONFIRMED'} if action=='rebook' else {'state':'SENT'}
        link='/reagendar-produccion/'+row['rebook_token']
        response=self.client.post(link,data={'date':next_day,'time':'11:00'})
        self.assertEqual(response.status_code,200)
        with billing.db() as c:
            self.assertEqual(c.execute('SELECT rebooked FROM production_payments').fetchone()['rebooked'],1)
            self.assertEqual(c.execute('SELECT appointment_date FROM billing_orders').fetchone()['appointment_date'],next_day)
        self.assertEqual(self.client.post(link,data={'date':next_day,'time':'12:00'}).status_code,409)
    def test_approved_payment_survives_calendar_failure_and_recovers(self):
        self.start()
        with (patch.object(p,'fulfill',side_effect=RuntimeError('calendar temporarily unavailable')),
              patch('sii_canary.start_emission',return_value=({},202))):
            response=self.client.get('/api/webpay/return?token_ws='+'a'*64)
            self.assertEqual(response.status_code,303)
            self.assertTrue(response.location.startswith(p.SITE+'/pago/'))
            pending=self.client.get(response.location)
            self.assertEqual(pending.status_code,200)
            self.assertIn('Tu pago fue aprobado',pending.get_data(as_text=True))
            self.assertEqual(pending.headers['Refresh'],'20')
            with billing.db() as c:
                self.assertEqual(c.execute('SELECT state FROM production_payments').fetchone()[0],'AUTHORIZED')
                self.assertEqual(c.execute('SELECT payment_status FROM billing_orders').fetchone()[0],'approved')
        confirmed=self.client.get(response.location)
        self.assertEqual(confirmed.status_code,200)
        with billing.db() as c:
            self.assertEqual(c.execute('SELECT state FROM production_payments').fetchone()[0],'CONFIRMED')
        self.assertEqual(self.g.commits,1)

    def test_paused_sales_cannot_create_transaction(self):
        with billing.db() as c:c.execute('INSERT INTO webpay_live_settings VALUES(1,?,0,?)',(self.g.config.commerce_code,billing.now()))
        self.assertEqual(self.start().status_code,503);self.assertEqual(self.g.creates,0)

if __name__=='__main__':unittest.main()

