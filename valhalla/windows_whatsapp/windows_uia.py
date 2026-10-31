"""Fail-closed Windows UI Automation adapter. No coordinate clicks or global Enter."""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode, quote
from core import normalized, Stopped, ConfirmationPending

DEFAULT_PROFILE = {
    'composer': {'control_type':'Edit','name_regex':r'(?i)^(Type a message|Escribe un mensaje|Escribir un mensaje|Mensaje)(?: (?:para|to) .+)?$'},
    'send': {'control_type':'Button','name_regex':r'(?i)^(Send|Enviar|Enviar mensaje)$'},
    'header': None,
    'outgoing': {'control_type':'Group','name_regex':r'(?i)^(Tú|You):$'},
    'sent_status_regex':r'(?i)^\s*(?:\d{1,2}:\d{2}\s+)?(sent|enviado|delivered|entregado|read|leído)\s*$',
    'pending_regex':r'(?i)\b(sending|enviando|pending|pendiente|retry|reintentar|failed|error)\b',
}

class VerificationError(Exception):
    def __init__(self,reason):self.safe_reason=reason;super().__init__(reason)

def require(condition,reason):
    if not condition:raise VerificationError(reason)

def matches(node,selector):
    i=node.element_info
    if selector.get('control_type') and i.control_type!=selector['control_type']:return False
    if selector.get('auto_id') is not None and i.automation_id!=selector['auto_id']:return False
    if selector.get('name') is not None and i.name!=selector['name']:return False
    if selector.get('name_regex') and not re.search(selector['name_regex'],i.name or ''):return False
    if selector.get('auto_id_regex') and not re.search(selector['auto_id_regex'],i.automation_id or ''):return False
    return node.is_visible() and node.is_enabled()

def native_chat_url(p,message):
    require(re.fullmatch(r'569\d{8}',p) is not None,'Teléfono inválido.')
    url='whatsapp://send?'+urlencode({'phone':p,'text':message},quote_via=quote,encoding='utf-8',errors='strict')
    require(len(url)<30000,'El mensaje excede el tamaño del enlace que admite el lanzador de Windows.')
    return url

def process_in_family(pid,root_pid,parent_pid):
    if not pid or not root_pid:return False
    seen=set()
    for _ in range(12):
        if pid==root_pid:return True
        if not pid or pid in seen:return False
        seen.add(pid);pid=parent_pid(pid)
    return False

def editor_text(value,paragraph=False):
    value=normalized(value)
    if not value.replace('\n',''):return ''
    return value.removesuffix('\n') if paragraph else value

def unique_controls(nodes):
    """WebView can expose the identical UIA element through many host branches."""
    result=[];seen=set()
    for node in nodes:
        runtime=tuple(node.element_info.runtime_id or ())
        # Never collapse genuinely distinct controls with equal names.
        key=('runtime',runtime) if runtime else ('object',id(node))
        if key not in seen:seen.add(key);result.append(node)
    return result

def verify_contact_details(texts,expected_name,p):
    require('Info. del contacto' in texts and 'Info. y número de teléfono' in texts,
            'La información no procede de la ficha del contacto.')
    require(expected_name in texts,'La ficha no corresponde al nombre de la conversación activa.')
    phones={re.sub(r'\D','',t) for t in texts if re.fullmatch(r'[+\d\s()\-\u200e\u200f]+',t) and re.fullmatch(r'569\d{8}',re.sub(r'\D','',t))}
    require(phones=={p},'El teléfono de la ficha no coincide exactamente con el destinatario solicitado.')

def control_tree(root,limit=4000):
    # Walk once per RuntimeId; repeated WebView branches otherwise create huge trees.
    pending=[root];seen=set();result=[]
    while pending:
        node=pending.pop(0);runtime=tuple(node.element_info.runtime_id or ())
        key=('runtime',runtime) if runtime else ('object',id(node))
        if key in seen:continue
        seen.add(key);result.append(node)
        require(len(result)<=limit,'El árbol UIA excede el límite de controles únicos. No se enviará.')
        children=node.children
        pending.extend(children() if callable(children) else children)
    return result

class WhatsApp:
    def __init__(self,folder,gate=lambda:None):
        # Imports stay lazy so the state machine can be tested on Linux.
        import psutil
        import win32gui
        import win32api
        import win32process
        from pywinauto import Desktop
        self.emit_key=lambda vk,flags:win32api.keybd_event(vk,0,flags,0)
        self.psutil=psutil;self.win32gui=win32gui;self.win32process=win32process;self.desktop=Desktop(backend='uia')
        self.folder=Path(folder);self.gate=gate;self.window=None;self.before=set()
        profile=self.folder/'uia-profile.json'
        if not profile.exists():profile.write_text(json.dumps(DEFAULT_PROFILE,indent=2,ensure_ascii=False),encoding='utf-8')
        self.profile=json.loads(profile.read_text(encoding='utf-8'))
        old_outgoing={'control_type':'ListItem','auto_id_regex':r'(?i)^(message-out|outgoingMessage)'}
        if self.profile.get('outgoing')==old_outgoing:self.profile['outgoing']=DEFAULT_PROFILE['outgoing'].copy()
        old_sent=r'(?i)^(sent|enviado|delivered|entregado|read|leído)(?:[ .,:·\d/\-apm]+)?$'
        if self.profile.get('sent_status_regex') in (old_sent,old_sent.replace('\\','\\\\')):self.profile['sent_status_regex']=DEFAULT_PROFILE['sent_status_regex']
        legacy=r'(?i)^(Type a message|Escribe un mensaje|Escribir un mensaje|Mensaje)$'
        if self.profile.get('composer')=={'control_type':'Edit','name_regex':legacy}:
            self.profile['composer']=DEFAULT_PROFILE['composer'].copy()
        profile.write_text(json.dumps(self.profile,ensure_ascii=False,indent=2),encoding='utf-8')
    def process_name(self,pid):
        try:return self.psutil.Process(pid).name().lower()
        except (self.psutil.Error,RuntimeError):return ''
    def inventory(self):
        # Inspect process identity first; never accept Chrome/Edge based on a title.
        native={'whatsapp.exe','whatsapp.root.exe'}
        candidates=[];metadata=[]
        for w in self.desktop.windows():
            try:
                if not w.is_visible():continue
                pid=w.process_id();name=self.process_name(pid)
                title=w.element_info.name or ''
                recognized=name in native
                if name=='applicationframehost.exe':
                    # Older Store apps may have a host-owned top-level window.
                    child_pids={n.element_info.process_id for n in w.descendants()}
                    recognized=any(self.process_name(p) in native for p in child_pids)
                if recognized or 'whatsapp' in title.lower() or 'whatsapp' in name:
                    metadata.append({'pid':pid,'process':name,'handle':w.handle,
                                     'title':title if recognized else '(ventana con WhatsApp en el título)',
                                     'native_identity':recognized,'foreground':self.win32gui.GetForegroundWindow()==w.handle})
                if recognized:candidates.append(w)
            except Exception:
                # One inaccessible window must not suppress the rest of the inventory.
                continue
        return candidates,metadata
    def find_window(self):
        windows,_=self.inventory()
        # Multiple WhatsApp windows can be legitimate (main window, call, pop-up).
        # Require a unique accessible composer; never choose an arbitrary window.
        editors=[]
        for w in windows:
            try:
                count=sum(matches(n,self.profile['composer']) for n in control_tree(w)[1:])
                if count==1:editors.append(w)
            except Exception:continue
        if len(editors)==1:windows=editors
        elif len(editors)>1:
            foreground=[w for w in editors if self.win32gui.GetForegroundWindow()==w.handle]
            if len(foreground)==1:windows=foreground
            else:windows=editors
        require(len(windows)==1,
                'No se identificó una ventana nativa única de WhatsApp ('+str(len(windows))+
                ' candidatas). Pulsa Diagnosticar UIA; ahora genera un informe incluso sin detectar la ventana.')
        self.window=windows[0];self.pid=self.window.process_id();self.hwnd=self.window.handle
    def owns_foreground(self):
        active=self.win32gui.GetForegroundWindow()
        if not active:return False
        if self.win32gui.GetAncestor(active,2)==self.win32gui.GetAncestor(self.hwnd,2):return True
        # WinUI/WebView may activate a different native HWND owned by the app's
        # child process. HWND equality alone falsely rejects that genuine editor.
        try:
            active_pid=self.win32process.GetWindowThreadProcessId(active)[1]
            return process_in_family(active_pid,self.pid,lambda pid:self.psutil.Process(pid).ppid())
        except Exception:return False
    def focus_report(self,stage):
        try:
            active=self.win32gui.GetForegroundWindow()
            active_pid=self.win32process.GetWindowThreadProcessId(active)[1] if active else 0
            record={'stage':stage,'whatsapp_hwnd':self.hwnd,'whatsapp_pid':self.pid,
                    'foreground_hwnd':active,'foreground_pid':active_pid,
                    'foreground_process':self.process_name(active_pid),'belongs_to_whatsapp':self.owns_foreground()}
            path=self.folder/'diagnostico-foco.json'
            records=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            records.append(record);path.write_text(json.dumps(records[-30:],ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:pass
    def activate_window(self):
        require(self.window is not None and self.win32gui.IsWindow(self.hwnd),'WhatsApp ya no está disponible.')
        require(self.window.process_id()==self.pid,'Cambió el proceso de WhatsApp.')
        self.focus_report('antes_de_activar')
        if self.win32gui.IsIconic(self.hwnd):self.win32gui.ShowWindow(self.hwnd,9)
        if not self.owns_foreground():
            try:
                self.win32gui.BringWindowToTop(self.hwnd)
                self.win32gui.SetForegroundWindow(self.hwnd)
            except Exception:pass
        if not self.owns_foreground():
            # Join input queues only for activation, and always detach afterwards.
            # This uses the standard Windows API; no foreground-lock settings change.
            import ctypes
            current=ctypes.windll.kernel32.GetCurrentThreadId()
            active=self.win32gui.GetForegroundWindow()
            target_thread=self.win32process.GetWindowThreadProcessId(self.hwnd)[0]
            active_thread=self.win32process.GetWindowThreadProcessId(active)[0] if active else 0
            attached=[]
            try:
                for thread in set((target_thread,active_thread)):
                    if thread and thread!=current and ctypes.windll.user32.AttachThreadInput(current,thread,True):attached.append(thread)
                self.win32gui.BringWindowToTop(self.hwnd)
                self.win32gui.SetForegroundWindow(self.hwnd)
            except Exception:pass
            finally:
                for thread in attached:ctypes.windll.user32.AttachThreadInput(current,thread,False)
        deadline=time.monotonic()+1
        while not self.owns_foreground() and time.monotonic()<deadline:self.gate();time.sleep(.05)
        self.focus_report('después_de_activar')
        require(self.owns_foreground(),'Windows no activó WhatsApp. Se guardó diagnostico-foco.json; no se pegará ni enviará.')
    def foreground(self):
        require(self.window is not None and self.win32gui.IsWindow(self.hwnd),'WhatsApp ya no está disponible.')
        require(self.window.process_id()==self.pid,'Cambió el proceso de WhatsApp.')
        if not self.owns_foreground():
            self.focus_report('foco_perdido')
            raise VerificationError('WhatsApp perdió el foco. Se guardó diagnostico-foco.json con el proceso activo. No se enviará.')
        require(self.window.is_visible() and self.window.is_enabled(),'Ventana de WhatsApp bloqueada o no disponible.')
    def nodes(self):return control_tree(self.window)[1:]
    def one(self,selector):
        found=[n for n in self.nodes() if matches(n,selector)]
        require(len(found)==1,'Control UIA ausente o ambiguo; revisa uia-profile.json y Diagnosticar.')
        return found[0]
    def read_edit(self,node):
        # WebView's empty editor contains a paragraph newline, not a real draft.
        # Prefer TextPattern (document contents), never the accessible name/placeholder.
        try:return editor_text(node.iface_text.DocumentRange.GetText(-1),paragraph=True)
        except Exception:
            try:return editor_text(node.iface_value.CurrentValue)
            except Exception:raise VerificationError('El campo no permite comprobar el texto mediante accesibilidad.')
    def focus_editor(self):
        self.activate_window();edit=self.one(self.profile['composer']);edit.click_input()
        self.foreground()
        require(edit.element_info.element.CurrentHasKeyboardFocus,'El editor de WhatsApp no tiene el foco; no se pegará ni enviará.')
        return edit
    def native_keys(self,editor,events):
        # Explicit virtual keys: Ctrl+V must not become Ctrl+Unicode/VK_PACKET.
        down=set()
        try:
            for key,flags in events:
                if not flags & 2:
                    self.foreground()
                    require(editor.element_info.element.CurrentHasKeyboardFocus,
                            'El editor perdió el foco antes de la tecla. No se continúa.')
                self.emit_key(key,flags)
                if flags & 2:down.discard(key)
                else:down.add(key)
        finally:
            # Release modifiers even if a keyboard event or focus check fails.
            for key in sorted(down,reverse=True):self.emit_key(key,2)
    def clipboard_text(self,editor):
        # Focus the actual contenteditable via its UIA rectangle. No fixed coordinates.
        # Then read the clipboard's plain Unicode selection, including embedded emoji.
        import win32clipboard
        import secrets
        editor=self.focus_editor()
        sentinel='valhalla-read-'+secrets.token_hex(16)
        previous=None
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):previous=win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.EmptyClipboard();win32clipboard.SetClipboardText(sentinel,win32clipboard.CF_UNICODETEXT)
        finally:win32clipboard.CloseClipboard()
        value=None
        try:
            self.native_keys(editor,[(0x11,0),(0x41,0),(0x41,2),(0x11,2)])
            self.native_keys(editor,[(0x11,0),(0x43,0),(0x43,2),(0x11,2)])
            deadline=time.monotonic()+1.5
            while time.monotonic()<deadline:
                self.gate();self.foreground()
                win32clipboard.OpenClipboard()
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                        candidate=win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                        if candidate!=sentinel:value=normalized(candidate);break
                finally:win32clipboard.CloseClipboard()
                time.sleep(.05)
            self.last_clipboard_read=value
            return value
        finally:
            # Arrow Right removes the selection without changing its contents.
            if self.owns_foreground() and editor.element_info.element.CurrentHasKeyboardFocus:
                self.native_keys(editor,[(0x27,0),(0x27,2)])
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                if previous is not None:win32clipboard.SetClipboardText(previous,win32clipboard.CF_UNICODETEXT)
            finally:win32clipboard.CloseClipboard()
    def message_matches(self,editor,message):
        current=self.read_edit(editor)
        if current==normalized(message):
            self.message_projection=current
            return True
        if current and self.clipboard_text(editor)==normalized(message):
            # This exact projection was independently bound to the complete Unicode
            # draft via clipboard; do not remove arbitrary emoji or whitespace.
            self.message_projection=current
            return True
        return False
    def editor_report(self,editor,message):
        report={'version':'1.10','expected':message,'accessible_name':editor.element_info.name,'clipboard_read':getattr(self,'last_clipboard_read',None)}
        for name,read in [('value_pattern',lambda:editor.iface_value.CurrentValue),
                          ('text_pattern',lambda:editor.iface_text.DocumentRange.GetText(-1))]:
            try:report[name]=read()
            except Exception as error:report[name+'_error']=type(error).__name__
        try:report['child_texts']=[n.element_info.name for n in control_tree(editor) if n.element_info.control_type=='Text']
        except Exception:pass
        (self.folder/'diagnostico-editor.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def open_chat(self,p,message):
        self.phone=p;self.chat_signature=None
        settle=time.monotonic()+1
        os.startfile(native_chat_url(p,message))
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            self.gate()
            try:
                self.find_window();self.activate_window();self.foreground()
                editor=self.one(self.profile['composer'])
                if time.monotonic()<settle:
                    time.sleep(.1);continue
                self.chat_signature=(tuple(editor.element_info.runtime_id or ()),editor.element_info.name)
                return
            except Stopped:raise
            except Exception as error:
                last_error=getattr(error,'safe_reason','No fue posible leer los controles UIA.')
                time.sleep(.4)
        raise VerificationError('WhatsApp no abrió un chat accesible. '+last_error)
    def verify_recipient(self,p,foreground_required=True):
        # User-selected workflow: rely on the requested native deep link, then bind
        # the resulting editor for the whole attempt. No contact-profile navigation.
        if foreground_required:self.foreground()
        else:
            # Post-send confirmation only reads UIA. Background focus is irrelevant.
            require(self.window is not None and self.win32gui.IsWindow(self.hwnd),'WhatsApp ya no está disponible.')
            require(self.window.process_id()==self.pid,'Cambió el proceso de WhatsApp.')
        require(p==self.phone,'El número solicitado cambió durante el intento.')
        editor=self.one(self.profile['composer'])
        signature=(tuple(editor.element_info.runtime_id or ()),editor.element_info.name)
        require(self.chat_signature is not None and signature==self.chat_signature,
                'Cambió la conversación después de abrir el enlace. No se enviará.')
        return True
    def prepare(self,message):
        # The native protocol loads Unicode text directly into the requested chat.
        # No clipboard or synthetic paste is used on this WhatsApp WebView build.
        deadline=time.monotonic()+12
        while True:
            self.gate();self.foreground()
            edit=self.one(self.profile['composer'])
            if self.message_matches(edit,message):break
            if time.monotonic()>=deadline:
                self.editor_report(edit,message)
                raise VerificationError('No se pudo verificar el texto completo, incluida su copia Unicode. Se guardó diagnostico-editor.json; no se enviará.')
            time.sleep(.2)
        self.verify_content(message)
        self.before={tuple(n.element_info.runtime_id) for n in self.outgoing()}
    def verify_content(self,message):
        self.foreground()
        edit=self.one(self.profile['composer'])
        if not self.message_matches(edit,message):
            self.editor_report(edit,message)
            raise VerificationError('El texto pegado no coincide. Se guardó diagnostico-editor.json; no se pulsará Enter.')
        self.expected_message=message
    def outgoing(self):
        rows=[]
        for marker in self.nodes():
            if not matches(marker,self.profile['outgoing']):continue
            row=marker
            for _ in range(5):
                if row.element_info.control_type=='DataItem':break
                row=row.parent()
                if row is None:break
            if row is not None and row.element_info.control_type=='DataItem':rows.append(row)
        return unique_controls(rows)
    def send(self):
        self.gate();self.foreground()
        self.verify_content(self.expected_message)
        button=self.one(self.profile['send'])
        self.foreground()
        try:button.invoke()
        except Exception:raise VerificationError('La acción Enviar quedó incierta. No se repetirá automáticamente.')
    def sent_report(self,message):
        # Read-only evidence; never clicks, focuses, copies or sends after Enviar.
        report={'version':'1.10','expected':message,'verified_draft_projection':getattr(self,'message_projection',None),'rows':[]}
        for row in self.outgoing():
            info={'runtime_id':row.element_info.runtime_id,'new':tuple(row.element_info.runtime_id) not in getattr(self,'before',set()),'controls':[]}
            for n in control_tree(row):
                record={'type':n.element_info.control_type,'name':n.element_info.name}
                for key,read in [('text',lambda:n.iface_text.DocumentRange.GetText(-1)),
                                 ('value',lambda:n.iface_value.CurrentValue),
                                 ('legacy_value',lambda:n.iface_legacy_iaccessible.CurrentValue),
                                 ('legacy_description',lambda:n.iface_legacy_iaccessible.CurrentDescription)]:
                    try:record[key]=read()
                    except Exception:pass
                info['controls'].append(record)
            report['rows'].append(info)
        path=self.folder/'diagnostico-envio.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return path
    def verify_sent(self,message):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            self.gate();self.verify_recipient(self.phone,foreground_required=False)
            for row in self.outgoing():
                if tuple(row.element_info.runtime_id) in self.before:continue
                children=control_tree(row)
                texts=[normalized(n.element_info.name or '') for n in children]
                content_values=list(texts)
                for n in children:
                    for read in (lambda:n.iface_text.DocumentRange.GetText(-1),lambda:n.iface_value.CurrentValue,lambda:n.iface_legacy_iaccessible.CurrentValue):
                        try:
                            value=read()
                            if isinstance(value,str):content_values.append(normalized(value).strip('\n'))
                        except Exception:pass
                status_labels=[t for t in texts if t and t!=normalized(message)]
                if normalized(message) not in content_values and getattr(self,'message_projection',None) not in content_values:continue
                if any(re.search(self.profile['pending_regex'],t) for t in status_labels):continue
                if any(re.fullmatch(self.profile['sent_status_regex'],t) for t in status_labels) and self.read_edit(self.one(self.profile['composer']))=='':
                    return
            time.sleep(.4)
        try:self.sent_report(message)
        except Exception:pass
        raise ConfirmationPending()
    def diagnose(self):
        # Always produce useful metadata, even when detection itself fails.
        windows,metadata=self.inventory()
        report={'version':'1.10','windows':metadata,'native_candidates':len(windows),'controls':[],
                'note':'No se escribió ni envió ningún mensaje durante este diagnóstico.'}
        try:
            self.find_window()
            for n in [self.window]+self.nodes():
                try:
                    i=n.element_info
                    report['controls'].append({'type':i.control_type,'auto_id':i.automation_id,
                                               'name':i.name,'runtime_id':i.runtime_id})
                except Exception:continue
        except Exception as error:
            report['detection_error']=getattr(error,'safe_reason','No fue posible leer los controles UIA.')
        path=self.folder/'diagnostico-uia-privado.json'
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return path
