"""Offline Chromium fixtures. All requests intercepted; never contacts SII."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from sii_final_control import inspect_final, finish, lock_preview, show_preview

class FinalGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start()
        cls.browser=cls.p.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.p.stop()
    def setUp(self):
        self.ctx=self.browser.new_context()
        self.ctx.route('**/*',lambda route:route.fulfill(status=200,body='<html><body></body></html>',content_type='text/html'))
        self.page=self.ctx.new_page();self.page.goto('https://eboleta.sii.cl/emitir/')
        self.ids={key:key for key in ['rut','nombre','direccion','email','telefono','detalle']}
        self.data={key:'fixture' for key in self.ids};self.data['order_id']='fixture-order'
        self.tmp=tempfile.TemporaryDirectory();self.result=Path(self.tmp.name)/'result.json'
    def tearDown(self):
        self.ctx.close();self.tmp.cleanup()
    def fixture(self, extra='', tag='form', amount='30.000', title='Boleta exenta'):
        fields=''.join(f'<input id="{key}" value="fixture">' for key in self.ids)
        self.page.set_content('<button>Emitir</button><button>7</button>'+f'<{tag} id="final"><p>{title} $ {amount}</p>'+fields+extra+'<button type="button" onclick="window.clicks=(window.clicks||0)+1">Emitir</button>'+f'</{tag}>')
    def test_visible_preview_blocks_click_submit_and_enter(self):
        self.fixture()
        lock_preview(self.page)
        self.page.locator('#final button').click()
        self.assertIsNone(self.page.evaluate('window.clicks'))
        self.assertTrue(inspect_final(self.page,self.ids,self.data)['valid'])
        show_preview(self.page, seconds=0)
        self.assertTrue(self.page.locator('#final button').is_disabled())
        self.assertTrue(self.page.locator('#psc-preview-banner').is_visible())
        self.assertFalse(self.page.locator('#final').evaluate("e=>e.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))"))
        self.assertFalse(self.page.locator('#nombre').evaluate("e=>e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))"))

    def test_native_form_and_preview_never_click(self):
        self.fixture()
        info=inspect_final(self.page,self.ids,self.data)
        self.assertTrue(info['valid']);self.assertTrue(info['native_form'])
        finish(self.page,self.ids,self.data,30000,self.result)
        self.assertEqual(json.loads(self.result.read_text())['status'],'prepared')
        self.assertIsNone(self.page.evaluate('window.clicks'))
    def test_react_container_and_ambiguity(self):
        self.fixture(tag='div')
        self.assertTrue(inspect_final(self.page,self.ids,self.data)['valid'])
        self.fixture(extra='<button>Emitir</button>',tag='div')
        self.assertFalse(inspect_final(self.page,self.ids,self.data)['valid'])
        self.fixture(extra='<button>7</button>',tag='div')
        self.assertFalse(inspect_final(self.page,self.ids,self.data)['valid'])
    def test_changed_receiver_disabled_and_wrong_host(self):
        self.fixture();self.page.locator('#nombre').fill('changed')
        self.assertFalse(inspect_final(self.page,self.ids,self.data)['valid'])
        self.fixture();self.page.locator('#final button').evaluate('(e)=>e.disabled=true')
        self.assertFalse(inspect_final(self.page,self.ids,self.data)['valid'])
        self.page.goto('https://example.invalid/')
        with self.assertRaises(RuntimeError):inspect_final(self.page,self.ids,self.data)
    def test_wrong_document_or_amount_cannot_click(self):
        for amount,title in [('1','Boleta exenta'),('30.000','Boleta afecta')]:
            self.fixture(amount=amount,title=title)
            finish(self.page,self.ids,self.data,30000,self.result,'fixture-order',Path(self.tmp.name)/'journal')
            self.assertEqual(json.loads(self.result.read_text())['status'],'blocked')
            self.assertIsNone(self.page.evaluate('window.clicks'))
    def test_click_is_uncertain_and_cannot_repeat(self):
        self.fixture()
        with patch.object(self.page,'wait_for_timeout'):
            finish(self.page,self.ids,self.data,30000,self.result,'fixture-order',Path(self.tmp.name)/'journal')
        self.assertEqual(self.page.evaluate('window.clicks'),1)
        result=json.loads(self.result.read_text())
        self.assertEqual(result['status'],'reconciliation');self.assertIsNone(result['emitted'])
        with self.assertRaises(FileExistsError):
            finish(self.page,self.ids,self.data,30000,self.result,'fixture-order',Path(self.tmp.name)/'journal')
        self.assertEqual(self.page.evaluate('window.clicks'),1)
    def test_panel_enqueues_preview_and_disables_final(self):
        root=Path(__file__).resolve().parents[1]/'psicologia-api'
        row=dict(order_id='fixture-order',name='Persona ficticia',created_at='2026-01-01',
            appointment_date='',appointment_time='',payment_status='approved',invoice_status='test',
            environment='integration',amount=30000,payment_method='Tarjeta',rut='12345678-5',
            address='Ficticia 123',email='test@example.invalid',phone='12345678',detail='Sesión')
        requests=[]
        def serve(route):
            path=route.request.url.split('/admin/boletas',1)[-1].split('?')[0]
            if path in ('','/billing_admin.js','/billing_admin.css'):
                name={'':'billing_admin.html','/billing_admin.js':'billing_admin.js','/billing_admin.css':'billing_admin.css'}[path]
                mime={'':'text/html','/billing_admin.js':'application/javascript','/billing_admin.css':'text/css'}[path]
                route.fulfill(body=(root/name).read_text(),content_type=mime)
            elif path=='/data':route.fulfill(json={'orders':[row]})
            elif path=='/sii-jobs':route.fulfill(json={'jobs':[],'final_enabled':False})
            elif path=='/fixture-order/sii-jobs':
                requests.append(route.request.post_data_json)
                route.fulfill(status=201,json={'job_id':'fixture','status':'queued'})
            else:route.fulfill(status=404,body='')
        self.ctx.unroute('**/*');self.ctx.route('**/*',serve)
        self.page.goto('https://psc.example.invalid/admin/boletas')
        button=self.page.get_by_role('button',name='Emitir boleta SII',exact=True)
        button.wait_for();self.assertTrue(button.is_disabled())
        self.page.get_by_role('button',name='Previsualizar prueba en SII (sin emitir)',exact=True).click()
        self.page.wait_for_function("document.getElementById('notice').textContent.includes('Compra enviada')")
        self.assertEqual(requests,[{'mode':'preview'}])

    def test_click_timeout_leaves_durable_uncertainty(self):
        self.fixture()
        with patch.object(self.page,'locator') as locator:
            locator.return_value.click.side_effect=TimeoutError()
            with self.assertRaises(TimeoutError):
                finish(self.page,self.ids,self.data,30000,self.result,'fixture-order',Path(self.tmp.name)/'journal')
        self.assertEqual(json.loads(self.result.read_text())['status'],'reconciliation')
        self.assertEqual(len(list((Path(self.tmp.name)/'journal').glob('*.attempt'))),1)

if __name__=='__main__':unittest.main()
