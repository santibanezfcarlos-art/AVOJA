"""Process/window detection tests, without starting Windows or WhatsApp."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

folder=Path(__file__).resolve().parents[1]/'windows_whatsapp'
spec=importlib.util.spec_from_file_location('core',folder/'core.py')
core=importlib.util.module_from_spec(spec);spec.loader.exec_module(core)
spec=importlib.util.spec_from_file_location('win_detection',folder/'windows_uia.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'core':core}):spec.loader.exec_module(module)

class Window:
    def __init__(self,pid,handle,title='WhatsApp',children=()):
        self.pid=pid;self.handle=handle;self.children=children
        self.element_info=SimpleNamespace(name=title,process_id=pid,control_type='Window',automation_id='',runtime_id=[handle])
    def process_id(self):return self.pid
    def is_visible(self):return True
    def is_enabled(self):return True
    def descendants(self):return list(self.children)

class Detection(unittest.TestCase):
    def make(self,windows,names,foreground=0):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        adapter.desktop=SimpleNamespace(windows=lambda:windows)
        adapter.psutil=SimpleNamespace(Error=RuntimeError,Process=lambda pid:SimpleNamespace(name=lambda:names[pid]))
        adapter.win32gui=SimpleNamespace(GetForegroundWindow=lambda:foreground)
        adapter.profile=module.DEFAULT_PROFILE
        return adapter
    def test_root_executable(self):
        adapter=self.make([Window(1,100)],{1:'WhatsApp.Root.exe'})
        adapter.find_window();self.assertEqual(adapter.hwnd,100)
    def test_multiple_windows_choose_unique_composer(self):
        editor=Window(1,1000,'Escribe un mensaje');editor.element_info.control_type='Edit'
        adapter=self.make([Window(1,100,children=[editor]),Window(1,200,'Llamada')],{1:'WhatsApp.exe'})
        adapter.find_window();self.assertEqual(adapter.hwnd,100)
    def test_browser_title_is_not_native_identity(self):
        adapter=self.make([Window(1,100,'WhatsApp Business - Google Chrome')],{1:'chrome.exe'})
        windows,info=adapter.inventory()
        self.assertEqual(windows,[]);self.assertFalse(info[0]['native_identity'])
        with self.assertRaises(module.VerificationError):adapter.find_window()
    def test_store_host_requires_native_child(self):
        child=Window(2,200)
        adapter=self.make([Window(1,100,children=[child])],{1:'ApplicationFrameHost.exe',2:'WhatsApp.exe'})
        adapter.find_window();self.assertEqual(adapter.hwnd,100)
    def test_unrecognized_host_is_rejected(self):
        adapter=self.make([Window(1,100)],{1:'ApplicationFrameHost.exe'})
        with self.assertRaises(module.VerificationError):adapter.find_window()
    def test_ambiguous_windows_are_rejected(self):
        adapter=self.make([Window(1,100),Window(1,200)],{1:'WhatsApp.exe'})
        with self.assertRaises(module.VerificationError):adapter.find_window()
    def test_diagnostic_is_written_even_without_window(self):
        adapter=self.make([Window(1,100,'WhatsApp - Chrome')],{1:'chrome.exe'})
        with tempfile.TemporaryDirectory() as tmp:
            adapter.folder=Path(tmp);path=adapter.diagnose();data=json.loads(path.read_text())
            self.assertEqual(data['native_candidates'],0)
            self.assertIn('detection_error',data);self.assertEqual(data['controls'],[])
            self.assertEqual(data['windows'][0]['process'],'chrome.exe')

if __name__=='__main__':unittest.main()
