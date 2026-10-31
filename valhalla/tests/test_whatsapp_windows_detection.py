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
    def test_duplicate_webview_controls_are_one_element(self):
        edit=Window(1,111,'Escribir un mensaje para Persona')
        edit.element_info.control_type='Edit'
        other=Window(1,222,'Escribir un mensaje para Otra persona')
        other.element_info.control_type='Edit'
        self.assertEqual(module.unique_controls([edit]*79),[edit])
        self.assertEqual(len(module.unique_controls([edit,other])),2)
        self.assertTrue(module.matches(edit,module.DEFAULT_PROFILE['composer']))
        self.assertFalse(module.matches(Window(1,333,'Buscar un chat o iniciar uno nuevo'),module.DEFAULT_PROFILE['composer']))
    def test_contact_phone_requires_profile_context_and_exact_phone(self):
        texts=['Info. del contacto','Info. y número de teléfono','Contacto prueba','+56 9 1111 1111']
        module.verify_contact_details(texts,'Contacto prueba','56911111111')
        for bad in (texts[:-1],texts+['+56 9 2222 2222'],texts[2:]):
            with self.assertRaises(module.VerificationError):module.verify_contact_details(bad,'Contacto prueba','56911111111')
        with self.assertRaises(module.VerificationError):module.verify_contact_details(texts,'Otro contacto','56911111111')
        with self.assertRaises(module.VerificationError):module.verify_contact_details(texts,'Contacto prueba','56922222222')
    def test_control_walk_deduplicates_runtime_ids(self):
        editor=Window(1,111,'Escribir un mensaje para Persona')
        root=Window(1,100,children=[editor]*79)
        self.assertEqual(module.control_tree(root),[root,editor])
    def test_empty_webview_paragraph_is_not_a_draft(self):
        for value in ('','\n','\r\n','\r','\n\n'):
            self.assertEqual(module.editor_text(value),'')
        self.assertEqual(module.editor_text('Borrador\r\n',paragraph=True),'Borrador')
        self.assertEqual(module.editor_text('¡Hola! 👋\r\nConexión 🌿\r\n',paragraph=True),'¡Hola! 👋\nConexión 🌿')
        self.assertEqual(module.editor_text(' '),' ')
    def test_send_invokes_only_unique_verified_button(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp);calls=[]
        adapter.gate=lambda:None;adapter.foreground=lambda:None
        adapter.expected_message='Prueba';adapter.verify_content=lambda message:calls.append('verified')
        adapter.profile=module.DEFAULT_PROFILE
        button=SimpleNamespace(invoke=lambda:calls.append('invoked'))
        adapter.one=lambda selector:button
        adapter.send();self.assertEqual(calls,['invoked'])
        def mismatch(message):raise module.VerificationError('mismatch')
        adapter.expected_message=None
        with self.assertRaises(module.VerificationError):adapter.send()
        self.assertEqual(calls,['invoked'])
    def test_native_link_preserves_unicode_and_line_breaks(self):
        from urllib.parse import urlparse,parse_qs
        msg='¡Hola, Ángela! 👋\nConexión 🌿 & +'
        url=module.native_chat_url('56911111111',msg)
        parsed=urlparse(url)
        self.assertEqual(parsed.scheme,'whatsapp')
        self.assertEqual(parse_qs(parsed.query),{'phone':['56911111111'],'text':[msg]})
        with self.assertRaises(module.VerificationError):module.native_chat_url('invalid',msg)
    def test_bound_chat_check_does_not_open_profile(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        editor=Window(1,111,'Escribir un mensaje para Persona')
        editor.element_info.control_type='Edit'
        adapter.foreground=lambda:None;adapter.one=lambda selector:editor
        adapter.profile=module.DEFAULT_PROFILE;adapter.phone='56911111111'
        adapter.chat_signature=(tuple(editor.element_info.runtime_id),editor.element_info.name)
        self.assertTrue(adapter.verify_recipient('56911111111'))
        with self.assertRaises(module.VerificationError):adapter.verify_recipient('56922222222')
        editor.element_info.name='Escribir un mensaje para Otro contacto'
        with self.assertRaises(module.VerificationError):adapter.verify_recipient('56911111111')
    def test_webview_process_family(self):
        parents={30:20,20:10,10:1,40:1,50:50}
        self.assertTrue(module.process_in_family(30,10,parents.get))
        self.assertTrue(module.process_in_family(10,10,parents.get))
        self.assertFalse(module.process_in_family(40,10,parents.get))
        self.assertFalse(module.process_in_family(50,10,parents.get))
        self.assertFalse(module.process_in_family(0,10,parents.get))
    def test_native_webview_foreground_is_accepted_but_unrelated_app_is_not(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        adapter.hwnd=100;adapter.pid=10
        adapter.win32gui=SimpleNamespace(GetForegroundWindow=lambda:200,GetAncestor=lambda h,k:h)
        adapter.win32process=SimpleNamespace(GetWindowThreadProcessId=lambda h:(1,30))
        adapter.psutil=SimpleNamespace(Process=lambda pid:SimpleNamespace(ppid=lambda:{30:20,20:10}.get(pid,0)))
        self.assertTrue(adapter.owns_foreground())
        adapter.win32process=SimpleNamespace(GetWindowThreadProcessId=lambda h:(1,40))
        self.assertFalse(adapter.owns_foreground())
    def test_paste_virtual_keys_and_modifier_cleanup(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp);events=[]
        editor=SimpleNamespace(element_info=SimpleNamespace(element=SimpleNamespace(CurrentHasKeyboardFocus=True)))
        adapter.foreground=lambda:None;adapter.emit_key=lambda vk,flags:events.append((vk,flags))
        adapter.native_keys(editor,[(0x11,0),(0x56,0),(0x56,2),(0x11,2)])
        self.assertEqual(events,[(0x11,0),(0x56,0),(0x56,2),(0x11,2)])
        events.clear()
        def fail_on_v(vk,flags):
            if vk==0x56 and flags==0:raise RuntimeError('failed')
            events.append((vk,flags))
        adapter.emit_key=fail_on_v
        with self.assertRaises(RuntimeError):adapter.native_keys(editor,[(0x11,0),(0x56,0)])
        self.assertEqual(events,[(0x11,0),(0x11,2)])
    def test_unicode_draft_verification_requires_exact_copy_when_uia_is_lossy(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        message='¡Hola! 👋\nConexión 🌿'
        adapter.read_edit=lambda edit:'¡Hola! 👋\n\nConexión '
        adapter.clipboard_text=lambda edit:message
        self.assertTrue(adapter.message_matches(None,message))
        self.assertEqual(adapter.message_projection,'¡Hola! 👋\n\nConexión ')
        adapter.clipboard_text=lambda edit:'¡Hola! 👋\nConexión '
        self.assertFalse(adapter.message_matches(None,message))
        adapter.clipboard_text=lambda edit:message+'�'
        self.assertFalse(adapter.message_matches(None,message))
        adapter.clipboard_text=lambda edit:None
        self.assertFalse(adapter.message_matches(None,message))
    def test_empty_editor_is_not_accepted_as_complete_message(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        adapter.read_edit=lambda edit:''
        adapter.clipboard_text=lambda edit:self.fail('Do not select/copy a blank editor')
        self.assertFalse(adapter.message_matches(None,'Prueba'))
    def test_post_send_chat_check_reads_background_without_activating(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp)
        editor=Window(1,111,'Escribir un mensaje para Persona')
        adapter.one=lambda selector:editor;adapter.profile=module.DEFAULT_PROFILE
        adapter.window=Window(1,100);adapter.hwnd=100;adapter.pid=1
        adapter.win32gui=SimpleNamespace(IsWindow=lambda h:True)
        adapter.phone='56911111111'
        adapter.chat_signature=(tuple(editor.element_info.runtime_id),editor.element_info.name)
        adapter.foreground=lambda:self.fail('Read-only confirmation must not require foreground')
        self.assertTrue(adapter.verify_recipient('56911111111',foreground_required=False))
        editor.element_info.name='Otra conversación'
        with self.assertRaises(module.VerificationError):adapter.verify_recipient('56911111111',foreground_required=False)
    def test_successful_post_send_confirmation_is_entirely_read_only(self):
        adapter=module.WhatsApp.__new__(module.WhatsApp);checks=[]
        message='¡Hola! 👋\nConexión 🌿'
        body=Window(1,201,message);body.element_info.control_type='Text'
        status=Window(1,202,'17:14 Entregado ');status.element_info.control_type='Button'
        row=Window(1,200,children=[body,status])
        adapter.gate=lambda:None;adapter.phone='56911111111';adapter.before=set()
        adapter.verify_recipient=lambda p,foreground_required=True:checks.append(foreground_required)
        adapter.outgoing=lambda:[row];adapter.profile=module.DEFAULT_PROFILE
        adapter.one=lambda selector:None;adapter.read_edit=lambda editor:''
        adapter.foreground=lambda:self.fail('No foreground check after send')
        adapter.activate_window=lambda:self.fail('No window activation after send')
        adapter.emit_key=lambda *args:self.fail('No keyboard actions after send')
        adapter.verify_sent(message)
        self.assertEqual(checks,[False])
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

class ClipboardRetry(unittest.TestCase):
    def test_busy_clipboard_retries(self):
        from unittest.mock import Mock
        adapter=object.__new__(module.WhatsApp);adapter.gate=lambda:None
        clipboard=Mock();clipboard.OpenClipboard.side_effect=[RuntimeError('busy'),None]
        with patch.object(module.time,'sleep'):
            adapter.open_clipboard(clipboard)
        self.assertEqual(clipboard.OpenClipboard.call_count,2)

    def test_busy_clipboard_timeout_is_specific(self):
        from unittest.mock import Mock
        adapter=object.__new__(module.WhatsApp);adapter.gate=lambda:None
        clipboard=Mock();clipboard.OpenClipboard.side_effect=RuntimeError('busy')
        with patch.object(module.time,'monotonic',side_effect=[0,3]):
            with self.assertRaises(module.VerificationError):adapter.open_clipboard(clipboard)

class UnavailableDetection(unittest.TestCase):
    def test_dedicated_unblock_action(self):
        node=SimpleNamespace(element_info=SimpleNamespace(control_type='Button',name='Desbloquear'))
        self.assertIn('bloqueado',module.unavailable_reason([node]))
    def test_invalid_number_requires_error_dialog(self):
        dialog=SimpleNamespace(element_info=SimpleNamespace(control_type='Dialog',name='Aviso'))
        text=SimpleNamespace(element_info=SimpleNamespace(control_type='Text',name='Este número no está en WhatsApp'))
        with patch.object(module,'control_tree',return_value=[dialog,text]):
            self.assertIn('sin cuenta',module.unavailable_reason([dialog]))

    def test_chat_text_is_not_a_block_or_invalid_number_signal(self):
        node=SimpleNamespace(element_info=SimpleNamespace(control_type='Text',name='Este número no tiene WhatsApp. Desbloquear'))
        self.assertIsNone(module.unavailable_reason([node]))

if __name__=='__main__':unittest.main()
