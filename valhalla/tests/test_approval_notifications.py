import base64
import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import Store,ADMIN_EMAIL,Problem
from notifications import NotificationDispatcher,notification_message,notification_html
from mailer import EmailDeliveryError

class ApprovalNotifications(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name)/'approval.sqlite3');self.store.configure_access('ApprovalAdminTest2026!')
        self.admin=self.store.session(self.store.login({'email':ADMIN_EMAIL,'password':'ApprovalAdminTest2026!'}))['id']
        self.uid=self.store.register({'name':'Profesional <Ejemplo>','email':'approval@example.cl','specialty':'Psicología','password':'ApprovalProTest2026!'})
    def upload(self,kind):
        self.store.upload(self.uid,{'kind':kind,'filename':'private-'+kind+'.pdf','content':base64.b64encode(b'private document fixture').decode()})
        with self.store.connection() as c:return c.execute('SELECT MAX(id) FROM documents WHERE user_id=?',(self.uid,)).fetchone()[0]
    def approve(self,did):self.store.admin_action(self.admin,{'action':'document','id':did,'decision':'approved','identity_confirmed':True})
    def jobs(self):
        with self.store.connection() as c:return [dict(row) for row in c.execute("SELECT * FROM notification_outbox WHERE kind='document_approved' ORDER BY id")]
    def test_separate_personal_notifications_for_identity_and_degree_without_duplicates(self):
        identity=self.upload('identity');degree=self.upload('degree');self.assertEqual(self.jobs(),[])
        self.approve(identity);self.approve(identity);self.approve(degree)
        jobs=self.jobs();self.assertEqual(len(jobs),2)
        for job,kind in zip(jobs,('identity','degree')):
            payload=json.loads(job['payload']);self.assertEqual(payload['document_kind'],kind);self.assertEqual(job['recipient'],'approval@example.cl')
            subject,text=notification_message('document_approved',payload,'https://valhallaoffice.cl');html=notification_html('document_approved',payload,'https://valhallaoffice.cl')
            self.assertIn('carnet de identidad' if kind=='identity' else 'certificado de título',subject)
            self.assertIn('¡Estás más cerca de tu reserva!',text);self.assertIn('correo confirmado y saldo disponible',html)
            self.assertIn('Profesional &lt;Ejemplo&gt;',html);self.assertNotIn('private-',html);self.assertNotIn('private document',job['payload'])
        with self.store.connection() as c:self.assertTrue(self.store.professional_verified(c,self.uid))
    def test_invalid_review_rejection_and_legacy_document_do_not_send_approval(self):
        did=self.upload('identity')
        with self.assertRaises(Problem):self.store.admin_action(self.admin,{'action':'document','id':did,'decision':'approved'})
        self.store.admin_action(self.admin,{'action':'document','id':did,'decision':'rejected','note':'Revisa ambos lados'})
        legacy=self.upload('accreditation');self.approve(legacy);self.assertEqual(self.jobs(),[])
    def test_concurrent_repeated_approval_sends_only_once(self):
        did=self.upload('identity')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(self.approve,[did,did]))
        self.assertEqual(len(self.jobs()),1)
    def test_new_review_after_rejection_can_notify_again_without_repeating_same_approval(self):
        did=self.upload('identity');self.approve(did)
        self.store.admin_action(self.admin,{'action':'document','id':did,'decision':'rejected','note':'Se requiere otra revisión'})
        self.approve(did);self.approve(did)
        self.assertEqual(len(self.jobs()),2)
        self.assertEqual([job['status'] for job in self.jobs()],['superseded','pending'])
        self.assertNotEqual(self.jobs()[0]['delivery_key'],self.jobs()[1]['delivery_key'])

    def test_queue_failure_rolls_back_approval(self):
        did=self.upload('identity')
        with patch.object(self.store,'queue_notification',side_effect=Problem('Queue failed')):
            with self.assertRaises(Problem):self.approve(did)
        with self.store.connection() as c:self.assertEqual(c.execute('SELECT status FROM documents WHERE id=?',(did,)).fetchone()[0],'pending')
        self.assertEqual(self.jobs(),[])
    def test_new_upload_rejection_and_name_change_supersede_unsent_approval(self):
        did=self.upload('identity');self.approve(did);self.upload('identity');self.assertEqual(self.jobs()[0]['status'],'superseded')
        degree=self.upload('degree');self.approve(degree);self.store.admin_action(self.admin,{'action':'document','id':degree,'decision':'rejected','note':'Se requiere revisión adicional'})
        self.assertEqual(self.jobs()[1]['status'],'superseded')
        latest=self.upload('identity');self.approve(latest);self.store.update_profile(self.uid,{'name':'Nombre actualizado','specialty':'Psicología'})
        self.assertEqual(self.jobs()[-1]['status'],'superseded')
    def test_provider_failure_keeps_approval_and_retries_with_same_delivery_key(self):
        did=self.upload('identity');self.approve(did)
        with self.store.connection(True) as c:c.execute("DELETE FROM notification_outbox WHERE kind!='document_approved'")
        class Delivery:
            configured=True;provider='test';fail=True;keys=[]
            def send_notification(self,email,kind,payload,key):
                self.keys.append(key)
                if self.fail:raise EmailDeliveryError('network_error')
        delivery=Delivery();dispatcher=NotificationDispatcher(self.store,delivery);dispatcher.dispatch_pending()
        self.assertEqual(self.jobs()[0]['status'],'retry')
        with self.store.connection(True) as c:
            self.assertEqual(c.execute('SELECT status FROM documents WHERE id=?',(did,)).fetchone()[0],'approved')
            c.execute('UPDATE notification_outbox SET next_attempt=0')
        delivery.fail=False;dispatcher.dispatch_pending();self.assertEqual(self.jobs()[0]['status'],'accepted');self.assertEqual(delivery.keys,[delivery.keys[0]]*2)
