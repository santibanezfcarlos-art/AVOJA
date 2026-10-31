"""Desktop UI. Tokens and queues stay in memory. All sending is user-started."""
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import uuid
import tkinter as tk
from tkinter import messagebox, ttk
from core import API, Journal, Runner, Stopped, TEST_MESSAGE, phone

FOLDER=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'ValhallaWhatsApp'

class App:
    def __init__(self,root):
        self.root=root;self.minimized_for_send=False;self.messages=queue.Queue();self.pause=threading.Event();self.stop=threading.Event();self.busy=False
        self.journal=Journal(FOLDER/'registro.sqlite3');self.api=None;self.data=None
        root.title('Valhalla · WhatsApp Windows v1.8');root.geometry('840x650')
        frame=ttk.Frame(root,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='1 · Prueba exclusivamente con tu propio número',font=('',12,'bold')).pack(anchor='w')
        self.number=tk.StringVar();ttk.Entry(frame,textvariable=self.number).pack(fill='x',pady=6)
        ttk.Label(frame,text=TEST_MESSAGE).pack(anchor='w')
        self.test_button=ttk.Button(frame,text='Enviar una prueba a mi número',command=self.test);self.test_button.pack(anchor='w',pady=6)
        ttk.Label(frame,text='2 · Conecta una campaña desde Administración → WhatsApp',font=('',12,'bold')).pack(anchor='w',pady=(15,0))
        self.token=tk.StringVar();ttk.Entry(frame,textvariable=self.token,show='•').pack(fill='x',pady=6)
        self.connect_button=ttk.Button(frame,text='Cargar selección y conteos actuales (sin enviar)',command=self.connect);self.connect_button.pack(anchor='w')
        self.summary=tk.StringVar(value='Sin campaña conectada.');ttk.Label(frame,textvariable=self.summary,wraplength=800).pack(anchor='w',pady=8)
        self.authorized=tk.BooleanVar(value=False)
        ttk.Checkbutton(frame,text='He autorizado esta campaña, su mensaje y estos destinatarios.',variable=self.authorized).pack(anchor='w')
        controls=ttk.Frame(frame);controls.pack(fill='x',pady=8)
        self.start_button=ttk.Button(controls,text='Revisar e iniciar campaña',command=self.campaign);self.start_button.pack(side='left')
        ttk.Button(controls,text='Pausar / continuar',command=self.toggle).pack(side='left',padx=8)
        ttk.Button(controls,text='Detener',command=self.stop.set).pack(side='left')
        self.diag_button=ttk.Button(controls,text='Diagnosticar UIA',command=self.diagnose);self.diag_button.pack(side='left',padx=8)
        ttk.Button(controls,text='Abrir registro local',command=lambda:os.startfile(FOLDER)).pack(side='left')
        self.logbox=tk.Text(frame,height=13,wrap='word',state='disabled');self.logbox.pack(fill='both',expand=True)
        ttk.Label(frame,text='WhatsApp debe permanecer visible y sin uso manual durante un intento. Detener no retira mensajes ya accionados.',wraplength=800).pack(anchor='w',pady=6)
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll)
    def log(self,text):self.messages.put(('log',text))
    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='log':
                    self.logbox.configure(state='normal');self.logbox.insert('end',time.strftime('%H:%M:%S')+' '+value+'\n');self.logbox.see('end');self.logbox.configure(state='disabled')
                elif kind=='connected':
                    self.data=value;c=value['counts'];self.summary.set(f"{value['campaign_name']} · {len(value['recipients'])} pendientes seleccionados. Contactos actuales: {c['total']} · con coincidencia {c['registered']} · sin coincidencia {c['not_registered']} · bajas {c['opted_out']}.")
                elif kind=='done':
                    self.busy=False
                    if self.minimized_for_send:
                        self.minimized_for_send=False;self.root.deiconify()
                    for b in (self.test_button,self.connect_button,self.start_button,self.diag_button):b.configure(state='normal')
        except queue.Empty:pass
        self.root.after(100,self.poll)
    def gate(self):
        if self.stop.is_set():raise Stopped()
        while self.pause.is_set():
            if self.stop.wait(.1):raise Stopped()
    def toggle(self):
        if self.pause.is_set():self.pause.clear();self.log('Continuando; se verificará la ventana otra vez.')
        else:self.pause.set();self.log('Pausa solicitada. No se iniciarán nuevas acciones de envío.')
    def task(self,fn,hide=False):
        if self.busy:return
        self.busy=True;self.pause.clear();self.stop.clear()
        self.minimized_for_send=hide
        if hide:self.root.iconify()
        for b in (self.test_button,self.connect_button,self.start_button,self.diag_button):b.configure(state='disabled')
        def work():
            # COM initialization is required on this worker, not only the Tk main thread.
            import pythoncom
            pythoncom.CoInitialize()
            try:fn()
            except Stopped:self.log('Detenido. Retomar omite los intentos ya registrados.')
            except Exception as e:self.log(getattr(e,'safe_reason','Operación detenida. No se reenvía. Revisa conexión, registro y diagnóstico UIA.'))
            finally:pythoncom.CoUninitialize();self.messages.put(('done',None))
        self.root.after(350 if hide else 0,lambda:threading.Thread(target=work,daemon=True).start())
    def adapter(self):
        from windows_uia import WhatsApp
        return WhatsApp(FOLDER,self.gate)
    def test(self):
        try:p=phone(self.number.get())
        except ValueError as e:messagebox.showerror('Número',str(e));return
        if self.journal.test_history(p) and not messagebox.askyesno('Nueva prueba manual', 'Ya hay una prueba registrada para este número. Revisa primero tu conversación. ¿Confirmas que la revisaste y autorizas UN NUEVO mensaje de prueba a tu propio teléfono? No se borrará el intento anterior.') :return
        if not messagebox.askyesno('Prueba única',f'¿Confirmas que +{p} es tu propio número?\nSe pulsará Enviar automáticamente una sola vez.\n\n{TEST_MESSAGE}'):return
        def work():
            Runner(self.journal,self.adapter(),self.gate,self.log).run_one('prueba-'+uuid.uuid4().hex,{'phone':p,'message':TEST_MESSAGE},test=True)
        self.task(work,hide=True)
    def connect(self):
        token=self.token.get().strip()
        if not token:return
        def work():
            candidate=API(token);candidate.flush(self.journal);data=candidate.call('queue')
            self.api=candidate;self.messages.put(('connected',data));self.log('Selección cargada. No se ha enviado ningún mensaje.')
        self.task(work)
    def campaign(self):
        if not self.journal.test_passed():messagebox.showinfo('Primero la prueba','Falta comprobar el envío de prueba en este computador.');return
        if not self.data or not self.api or not self.authorized.get():messagebox.showinfo('Selección','Conecta una campaña y confirma su autorización.');return
        recipients=self.data['recipients']
        if not recipients:return
        review=tk.Toplevel(self.root);review.title('Revisar campaña antes de enviar');review.geometry('700x550')
        text=tk.Text(review,wrap='word');text.pack(fill='both',expand=True)
        text.insert('end',self.data['campaign_name']+'\n\nMensaje personalizado de ejemplo:\n'+recipients[0]['message']+'\n\nDestinatarios seleccionados:\n'+'\n'.join(r['name']+' · +'+r['phone'] for r in recipients));text.configure(state='disabled')
        def start():
            review.destroy();self.authorized.set(False)
            def work():
                self.api.flush(self.journal);fresh=self.api.call('queue')
                # Queue is refreshed on every run; server claims recheck every individual.
                runner=Runner(self.journal,self.adapter(),self.gate,self.log,self.api)
                for item in fresh['recipients']:
                    self.gate();runner.run_one(fresh['campaign_id'],item)
                    for _ in range(30):self.gate();time.sleep(.1)
                self.log('Cola finalizada. Comprobado en interfaz no significa entregado.')
            self.task(work,hide=True)
        ttk.Button(review,text='Iniciar envío de esta selección',command=start).pack(pady=10)
        ttk.Button(review,text='Cancelar',command=review.destroy).pack()
    def diagnose(self):
        def work():
            path=self.adapter().diagnose();self.log('Diagnóstico privado guardado en '+str(path)+'. Puede contener conversaciones; no se publica ni sube automáticamente.')
        self.task(work)
    def close(self):
        self.stop.set()
        if self.busy:
            messagebox.showinfo('Deteniendo','Espera a que termine el intento actual y vuelve a cerrar.');return
        self.root.destroy()

if __name__=='__main__':
    if sys.platform!='win32':raise SystemExit('Este programa se ejecuta en Windows 10/11.')
    FOLDER.mkdir(parents=True,exist_ok=True)
    import msvcrt
    # Interprocess lock prevents a second window from sharing the same sending journal.
    lock=open(FOLDER/'runner.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise SystemExit('Ya hay una instancia del programa abierta.')
    root=tk.Tk();App(root);root.mainloop()
