"""Fail-closed Windows UI Automation adapter. No coordinate clicks or global Enter."""
import json
import os
import re
import time
from pathlib import Path
from core import normalized, Stopped

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
        from pywinauto import Desktop
        self.psutil=psutil;self.win32gui=win32gui;self.desktop=Desktop(backend='uia')
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
    def foreground(self):
        require(self.window is not None and self.win32gui.IsWindow(self.hwnd),'WhatsApp ya no está disponible.')
        require(self.window.process_id()==self.pid,'Cambió el proceso de WhatsApp.')
        active=self.win32gui.GetForegroundWindow()
        require(self.win32gui.GetAncestor(active,2)==self.win32gui.GetAncestor(self.hwnd,2),'WhatsApp perdió el primer plano. No se enviará.')
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
        self.foreground();edit=self.one(self.profile['composer']);edit.set_focus()
        self.foreground()
        require(edit.element_info.element.CurrentHasKeyboardFocus,'El editor de WhatsApp no tiene el foco; no se pegará ni enviará.')
        return edit
    def open_chat(self,p):
        self.phone=p;self.chat_signature=None
        settle=time.monotonic()+1
        os.startfile('whatsapp://send?phone='+p)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            self.gate()
            try:
                self.find_window();self.window.set_focus();self.foreground()
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
    def verify_recipient(self,p):
        # User-selected workflow: rely on the requested native deep link, then bind
        # the resulting editor for the whole attempt. No contact-profile navigation.
        self.foreground()
        require(p==self.phone,'El número solicitado cambió durante el intento.')
        editor=self.one(self.profile['composer'])
        signature=(tuple(editor.element_info.runtime_id or ()),editor.element_info.name)
        require(self.chat_signature is not None and signature==self.chat_signature,
                'Cambió la conversación después de abrir el enlace. No se enviará.')
        return True
    def prepare(self,message):
        self.foreground();edit=self.one(self.profile['composer'])
        require(self.read_edit(edit)=='','Hay un borrador previo. No se sobrescribirá ni enviará.')
        import win32clipboard
        edit=self.focus_editor()
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(message,win32clipboard.CF_UNICODETEXT)
        finally:win32clipboard.CloseClipboard()
        self.gate();self.foreground()
        require(edit.element_info.element.CurrentHasKeyboardFocus,'Cambió el foco antes de pegar; no se pegará.')
        edit.type_keys('^v',set_foreground=False)
        deadline=time.monotonic()+4
        while self.read_edit(edit)!=normalized(message) and time.monotonic()<deadline:
            self.gate();self.foreground();time.sleep(.1)
        self.verify_content(message)
        self.before={tuple(n.element_info.runtime_id) for n in self.outgoing()}
    def verify_content(self,message):
        self.foreground()
        require(self.read_edit(self.one(self.profile['composer']))==normalized(message),'El borrador no coincide exactamente: texto, emojis o saltos de línea.')
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
        # Enter is explicitly targeted at the verified composer, never an arbitrary active window.
        self.gate();edit=self.focus_editor()
        self.verify_content(self.expected_message)
        self.foreground()
        require(edit.element_info.element.CurrentHasKeyboardFocus,'Cambió el foco antes de Enter. El intento queda incierto.')
        try:edit.type_keys('{ENTER}',set_foreground=False)
        except Exception:raise VerificationError('No se confirmó la acción Enter. El intento queda incierto; no se repetirá.')
    def verify_sent(self,message):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            self.gate();self.verify_recipient(self.phone)
            for row in self.outgoing():
                if tuple(row.element_info.runtime_id) in self.before:continue
                children=control_tree(row)
                texts=[normalized(n.element_info.name or '') for n in children]
                content_values=list(texts)
                for n in children:
                    try:content_values.append(normalized(n.iface_text.DocumentRange.GetText(-1)).strip('\n'))
                    except Exception:pass
                status_labels=[t for t in texts if t and t!=normalized(message)]
                if normalized(message) not in content_values:continue
                if any(re.search(self.profile['pending_regex'],t) for t in status_labels):continue
                if any(re.fullmatch(self.profile['sent_status_regex'],t) for t in status_labels) and self.read_edit(self.one(self.profile['composer']))=='':
                    return
            time.sleep(.4)
        raise VerificationError('Enviar fue accionado, pero no se comprobó una nueva burbuja saliente con texto exacto y estado enviado. No reenviar.')
    def diagnose(self):
        # Always produce useful metadata, even when detection itself fails.
        windows,metadata=self.inventory()
        report={'version':'1.4','windows':metadata,'native_candidates':len(windows),'controls':[],
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
