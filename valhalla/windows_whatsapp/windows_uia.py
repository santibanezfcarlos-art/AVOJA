"""Fail-closed Windows UI Automation adapter. No coordinate clicks or global Enter."""
import json
import os
import re
import time
from pathlib import Path
from core import normalized

DEFAULT_PROFILE = {
    'composer': {'control_type':'Edit','name_regex':r'(?i)^(Type a message|Escribe un mensaje|Escribir un mensaje|Mensaje)$'},
    'send': {'control_type':'Button','name_regex':r'(?i)^(Send|Enviar|Enviar mensaje)$'},
    'header': None,
    'outgoing': {'control_type':'ListItem','auto_id_regex':r'(?i)^(message-out|outgoingMessage)'},
    'sent_status_regex':r'(?i)^(sent|enviado|delivered|entregado|read|leído)(?:[ .,:·\d/\-apm]+)?$',
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
    def find_window(self):
        windows=[]
        for w in self.desktop.windows():
            try:
                proc=self.psutil.Process(w.process_id())
                if proc.name().lower()=='whatsapp.exe' and w.is_visible():windows.append(w)
            except (self.psutil.Error,RuntimeError):continue
        require(len(windows)==1,'No hay una única ventana visible del proceso WhatsApp.exe.')
        self.window=windows[0];self.pid=self.window.process_id();self.hwnd=self.window.handle
    def foreground(self):
        require(self.window is not None and self.win32gui.IsWindow(self.hwnd),'WhatsApp ya no está disponible.')
        require(self.window.process_id()==self.pid,'Cambió el proceso de WhatsApp.')
        require(self.win32gui.GetForegroundWindow()==self.hwnd,'WhatsApp perdió el primer plano. No se enviará.')
        require(self.window.is_visible() and self.window.is_enabled(),'Ventana de WhatsApp bloqueada o no disponible.')
    def nodes(self):return self.window.descendants()
    def one(self,selector):
        found=[n for n in self.nodes() if matches(n,selector)]
        require(len(found)==1,'Control UIA ausente o ambiguo; revisa uia-profile.json y Diagnosticar.')
        return found[0]
    def read_edit(self,node):
        try:return normalized(node.iface_value.CurrentValue)
        except Exception:
            try:return normalized(node.iface_text.DocumentRange.GetText(-1))
            except Exception:raise VerificationError('El campo no permite comprobar el texto mediante accesibilidad.')
    def open_chat(self,p):
        self.phone=p
        os.startfile('whatsapp://send?phone='+p)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            self.gate()
            try:
                self.find_window();self.window.set_focus();self.foreground()
                self.one(self.profile['composer']);return
            except Exception:time.sleep(.4)
        raise VerificationError('WhatsApp no abrió un chat accesible dentro de 25 segundos.')
    def verify_recipient(self,p):
        self.foreground()
        composer=self.one(self.profile['composer']); cr=composer.rectangle()
        selector=self.profile.get('header')
        if selector:
            header=self.one(selector)
            # The selector identifies the chat header; its current accessible name MUST expose the phone.
            name=header.element_info.name or ''
            require(re.sub(r'\D','',name)==p,'El encabezado no muestra exactamente el teléfono solicitado.')
        else:
            candidates=[]
            for node in self.nodes():
                # A button in the chat header, above and aligned with the composer. Never body/list text.
                if node.element_info.control_type!='Button' or not node.is_visible():continue
                name=node.element_info.name or '';r=node.rectangle()
                if re.fullmatch(r'[+\d\s()\-\u200e\u200f]+',name) and re.sub(r'\D','',name)==p and r.left>=cr.left-10 and r.bottom<cr.top:
                    candidates.append(node)
            require(len(candidates)==1,'No se puede verificar el número en un encabezado de chat único. No se usa el nombre como identidad.')
            header=candidates[0]
        self.header_id=tuple(header.element_info.runtime_id)
        return True
    def prepare(self,message):
        self.foreground();edit=self.one(self.profile['composer'])
        require(self.read_edit(edit)=='','Hay un borrador previo. No se sobrescribirá ni enviará.')
        # ValuePattern writes Unicode directly; it does not press keys into another window or alter the clipboard.
        try:edit.iface_value.SetValue(message)
        except Exception:
            # Some WhatsApp versions expose a read-only TextPattern. Paste only after
            # verifying the exact UIA element owns keyboard focus in WhatsApp.
            import win32clipboard
            edit.set_focus();self.foreground()
            require(edit.element_info.element.CurrentHasKeyboardFocus,'El editor no tiene el foco de teclado; no se pegará.')
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(message,win32clipboard.CF_UNICODETEXT)
            finally:win32clipboard.CloseClipboard()
            self.gate();self.foreground()
            require(edit.element_info.element.CurrentHasKeyboardFocus,'Cambió el foco antes de pegar; no se pegará.')
            edit.type_keys('^v',set_foreground=False)
            deadline=time.monotonic()+3
            while self.read_edit(edit)!=normalized(message) and time.monotonic()<deadline:time.sleep(.1)
        self.verify_content(message)
        self.before={tuple(n.element_info.runtime_id) for n in self.outgoing()}
    def verify_content(self,message):
        self.foreground()
        require(self.read_edit(self.one(self.profile['composer']))==normalized(message),'El borrador no coincide exactamente: texto, emojis o saltos de línea.')
        self.one(self.profile['send'])
    def outgoing(self):
        return [n for n in self.nodes() if matches(n,self.profile['outgoing'])]
    def send(self):
        self.foreground();button=self.one(self.profile['send'])
        # Invoke directly on the accessibility element. No mouse coordinates and no Enter.
        try:button.invoke()
        except Exception:raise VerificationError('La acción Enviar no pudo confirmarse. El intento queda incierto.')
    def verify_sent(self,message):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            self.gate();self.verify_recipient(self.phone)
            for row in self.outgoing():
                if tuple(row.element_info.runtime_id) in self.before:continue
                children=row.descendants()
                texts=[normalized(n.element_info.name or '') for n in children]
                status_labels=[t for t in texts if t and t!=normalized(message)]
                if normalized(message) not in texts:continue
                if any(re.search(self.profile['pending_regex'],t) for t in status_labels):continue
                if any(re.fullmatch(self.profile['sent_status_regex'],t) for t in status_labels) and self.read_edit(self.one(self.profile['composer']))=='':
                    return
            time.sleep(.4)
        raise VerificationError('Enviar fue accionado, pero no se comprobó una nueva burbuja saliente con texto exacto y estado enviado. No reenviar.')
    def diagnose(self):
        self.find_window()
        # Local-only diagnostic may contain private chat text. Never uploaded automatically.
        rows=[]
        for n in [self.window]+self.nodes():
            i=n.element_info
            rows.append({'type':i.control_type,'auto_id':i.automation_id,'name':i.name,'runtime_id':i.runtime_id})
        path=self.folder/'diagnostico-uia-privado.json'
        path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        return path
