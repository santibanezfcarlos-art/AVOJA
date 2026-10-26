import importlib.util,os,sys,types,unittest
from unittest.mock import patch
from pathlib import Path
# No browser is launched by these tests.
if importlib.util.find_spec('playwright') is None:
 p=types.ModuleType('playwright');m=types.ModuleType('playwright.sync_api');m.sync_playwright=lambda:None
 sys.modules['playwright']=p;sys.modules['playwright.sync_api']=m
spec=importlib.util.spec_from_file_location('activation_worker',Path(__file__).parent/'tools/fonasa_live_bridge.py')
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
class Button:
 def __init__(self,row,reverse=False):self.row=row;self.reverse=reverse
 def count(self):return int(self.row.active) if self.reverse else 1
 def is_visible(self):return True
 def is_enabled(self):return True
 def click(self,**kwargs):
  self.row.clicks+=1
  if self.row.fail:raise TimeoutError()
  self.row.active=True
class Row:
 def __init__(self,active=False,fail=False):self.active=active;self.fail=fail;self.clicks=0
 def get_by_role(self,role,name):return Button(self,'Desactivar' in name.pattern)
 def locator(self,path):return self
 def all_inner_texts(self):return ['123456','12345678-5','Activa' if self.active else 'Pendiente']
class Page:
 def __init__(self,row):self.row=row
 def on(self,*args):pass
 def locator(self,path):return self
 def all(self):return [self.row]
class Tests(unittest.TestCase):
 def test_positive_state_requires_evidence(self):
  self.assertFalse(f._row_status(Row()))
  self.assertTrue(f._row_status(Row(active=True)))
 def test_click_and_observed_result(self):
  row=Row();result=f._activate_matched(Page(row),row,'12345678-5','123456')
  self.assertEqual(row.clicks,1);self.assertEqual(result['state'],'activated');self.assertTrue(result['activated'])
 def test_timeout_is_not_success(self):
  row=Row(fail=True);result=f._activate_matched(Page(row),row,'12345678-5','123456')
  self.assertFalse(result['activated']);self.assertEqual(result['state'],'activation_unconfirmed')
 def test_disabled_route_never_launches_job(self):
  from flask import Flask
  app=Flask(__name__);app.register_blueprint(f.bp)
  with patch.dict(os.environ,{'PSC_FONASA_BRIDGE_KEY':'k'*50,'PSC_FONASA_ACTIVATION_ENABLED':'false'}),patch.object(f,'_start_job') as job:
   response=app.test_client().post('/bridge/fonasa/activate',json={},headers={'Authorization':'Bearer '+'k'*50})
  self.assertEqual(response.status_code,503);job.assert_not_called()

class ReadonlyVerificationTests(unittest.TestCase):
 def test_active_record_can_be_verified_without_pressing_activate_or_deactivate(self):
  row=Row(active=True)
  class Menu:
   @property
   def first(self):return self
   def count(self):return 1
   def is_visible(self):return True
   def click(self,**kwargs):pass
  class Field:
   def wait_for(self,**kwargs):pass
   def fill(self,*args):pass
   def click(self,**kwargs):pass
  class Inquiry(Page):
   def get_by_text(self,*args):return Menu()
   def locator(self,path):return self if path=='table:visible tr' else Field()
  result=f._verify_activated_view(Inquiry(row),'12345678-5','123456')
  self.assertTrue(result['activated']);self.assertEqual(row.clicks,0)
