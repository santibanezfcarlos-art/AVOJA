"""Queue isolation, authentication, duplicate and crash safety; no SII calls."""
from concurrent.futures import ThreadPoolExecutor
import unittest
import test_billing
import billing
import main

class SiiJobsTests(unittest.TestCase):
    order = test_billing.BillingTests.order
    mark = test_billing.BillingTests.mark
    tearDown = test_billing.BillingTests.tearDown
    def setUp(self):
        test_billing.BillingTests.setUp(self)
        self.headers={'X-Billing-Action':'manual-invoice'}
        r=self.client.post('/admin/boletas/sii-agent-token',auth=self.auth,headers=self.headers)
        self.agent={'Authorization':'Bearer '+r.json['token']}
    def enqueue(self, order='one', mode='preview'):
        return self.client.post('/admin/boletas/'+order+'/sii-jobs',auth=self.auth,headers=self.headers,json={'mode':mode})
    def approved(self, order='one', environment='production'):
        token=self.order(order,environment)
        billing.record_result(order,token,30000,True,'VD')
    def claim(self):
        return self.client.post('/api/sii-agent/claim',json={},headers=self.agent)
    def test_final_disabled_and_fake_payment_blocked(self):
        self.approved()
        self.assertEqual(self.enqueue(mode='issue').status_code,409)
        self.approved('test','integration')
        self.assertEqual(self.enqueue('test').status_code,409)
        self.order('unpaid','production')
        self.assertEqual(self.enqueue('unpaid').status_code,409)
        self.assertEqual(self.enqueue().status_code,201)
    def test_no_duplicate_claim_or_manual_folio_race(self):
        self.approved()
        job=self.enqueue().json['job_id']
        self.assertEqual(self.enqueue().status_code,409)
        self.assertEqual(self.mark('one').status_code,409)
        claimed=self.claim().json['job']
        self.assertEqual(claimed['id'],job)
        self.assertEqual(claimed['data']['monto'],30000)
        self.assertEqual(claimed['data']['email'],self.person['email'])
        self.assertIsNone(self.claim().json['job'])  # Crash never redelivers.
        result={'status':'prepared','emitted':False}
        for _ in range(2):
            self.assertEqual(self.client.post('/api/sii-agent/jobs/'+job+'/result',headers=self.agent,json=result).status_code,200)
        with billing.db() as con:
            row=con.execute("SELECT * FROM billing_orders WHERE order_id='one'").fetchone()
            self.assertEqual(row['invoice_status'],'pending')
            self.assertIsNone(row['folio'])
    def test_concurrent_queue_single_winner(self):
        self.approved()
        def enqueue(_):
            with main.app.test_client() as client:
                return client.post('/admin/boletas/one/sii-jobs',auth=self.auth,headers=self.headers,json={}).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sorted(pool.map(enqueue,range(4))),[201,409,409,409])
    def test_auth_csrf_and_forged_success(self):
        self.approved()
        self.assertEqual(self.client.post('/api/sii-agent/claim',json={}).status_code,401)
        self.assertEqual(self.client.post('/admin/boletas/one/sii-jobs',auth=self.auth,json={}).status_code,403)
        job=self.enqueue().json['job_id'];self.claim()
        self.assertEqual(self.client.post('/api/sii-agent/jobs/'+job+'/result',headers=self.agent,json={'status':'issued','emitted':True,'folio':'123'}).status_code,400)
        another=self.client.post('/admin/boletas/sii-agent-token',auth=self.auth,headers=self.headers).json['token']
        self.assertEqual(self.client.post('/api/sii-agent/jobs/'+job+'/result',headers={'Authorization':'Bearer '+another},json={'status':'prepared','emitted':False}).status_code,409)
    def test_revalidation_at_claim(self):
        self.approved();self.enqueue()
        with billing.db() as con:
            con.execute("UPDATE billing_orders SET invoice_status='issued',folio='44' WHERE order_id='one'")
        self.assertIsNone(self.claim().json['job'])
    def test_close_requires_stopped_agent(self):
        self.approved();job=self.enqueue().json['job_id'];self.claim()
        url='/admin/boletas/sii-jobs/'+job+'/close'
        self.assertEqual(self.client.post(url,auth=self.auth,headers=self.headers,json={}).status_code,400)
        self.assertEqual(self.client.post(url,auth=self.auth,headers=self.headers,json={'agent_stopped':True}).status_code,200)
        self.assertEqual(self.enqueue().status_code,201)

if __name__=='__main__':unittest.main()
