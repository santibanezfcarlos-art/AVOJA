"""Portable sending state machine. Does not import Windows libraries."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TEST_MESSAGE = '¡Hola! 👋 Prueba de Valhalla Office.\nEmojis, tildes y conexión: todo en orden. 🌿'

def phone(value):
    p=re.sub(r'\D','',value)
    if len(p)==9 and p.startswith('9'): p='56'+p
    if not re.fullmatch(r'569\d{8}',p): raise ValueError('Usa tu móvil chileno: +56 9 XXXX XXXX.')
    return p

def normalized(value):
    return value.replace('\r\n','\n').replace('\r','\n')

class Journal:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS attempts (
              campaign TEXT, phone TEXT, message_hash TEXT, attempt TEXT,
              state TEXT, reason TEXT, PRIMARY KEY(campaign,phone));
              CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY, attempt TEXT,status TEXT,reason TEXT);
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);''')
            # Preserve recovery as a durable result to synchronize on reconnect.
            c.execute("INSERT INTO outbox(attempt,status,reason) SELECT attempt,'runner_uncertain','Interrupción: revisión manual requerida' FROM attempts WHERE attempt!='test' AND state IN ('runner_claimed','runner_prepared','runner_actioned')")
            # Crash recovery is conservative, even if the click probably never happened.
            c.execute("UPDATE attempts SET state='runner_uncertain',reason='Interrupción: revisión manual requerida' WHERE state IN ('runner_claimed','runner_prepared','runner_actioned')")
    def db(self): return sqlite3.connect(self.path)
    def exists(self,campaign,p):
        with self.db() as c:return bool(c.execute('SELECT 1 FROM attempts WHERE campaign=? AND phone=?',(str(campaign),p)).fetchone())
    def begin(self,campaign,p,message,attempt):
        with self.db() as c:c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(str(campaign),p,hashlib.sha256(message.encode()).hexdigest(),attempt,'runner_claimed',''))
    def state(self,campaign,p,state,reason='',remote=False):
        with self.db() as c:
            row=c.execute('SELECT attempt FROM attempts WHERE campaign=? AND phone=?',(str(campaign),p)).fetchone()
            c.execute('UPDATE attempts SET state=?,reason=? WHERE campaign=? AND phone=?',(state,reason,str(campaign),p))
            if remote:c.execute('INSERT INTO outbox(attempt,status,reason) VALUES(?,?,?)',(row[0],state,reason))
    def pending(self):
        with self.db() as c:return c.execute('SELECT id,attempt,status,reason FROM outbox ORDER BY id').fetchall()
    def ack(self,id):
        with self.db() as c:c.execute('DELETE FROM outbox WHERE id=?',(id,))
    def test_history(self,p):
        with self.db() as c:return c.execute("SELECT state FROM attempts WHERE campaign LIKE 'prueba-%' AND phone=?",(p,)).fetchall()
    def test_passed(self):
        with self.db() as c:return bool(c.execute("SELECT 1 FROM meta WHERE key='test_passed'").fetchone())
    def pass_test(self):
        with self.db() as c:c.execute("INSERT OR REPLACE INTO meta VALUES('test_passed','1')")

class API:
    def __init__(self,token):self.token=token.strip()
    def call(self,op,**body):
        request=Request('https://valhallaoffice.cl/api/whatsapp-runner/'+op,
            data=json.dumps(body,ensure_ascii=False).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'},method='POST')
        try:
            with urlopen(request,timeout=20) as response:return json.load(response)
        except HTTPError as e:
            try: detail=json.load(e).get('error','Solicitud rechazada')
            except Exception: detail='Solicitud rechazada'
            raise RuntimeError(detail) from None
    def flush(self,journal):
        for id,attempt,status,reason in journal.pending():
            self.call('event',attempt=attempt,status=status,reason=reason);journal.ack(id)

class Stopped(Exception): pass

class Runner:
    def __init__(self,journal,adapter,gate,log,api=None):
        self.j=journal;self.ui=adapter;self.gate=gate;self.log=log;self.api=api
    def run_one(self,campaign,item,test=False):
        p=phone(item['phone']);message=item['message']; aid='test'; actioned=False; confirmed=False
        if self.j.exists(campaign,p):self.log('Omitido: ya existe registro local para este teléfono.');return False
        self.gate()
        if self.api:
            claim=self.api.call('claim',phone=p)
            if claim['phone']!=p or claim['message']!=message:raise RuntimeError('La cola cambió. Detenido.')
            aid=claim['attempt']
        self.j.begin(campaign,p,message,aid)
        def record(state,reason=''):
            self.j.state(campaign,p,state,reason,remote=bool(self.api))
            if self.api:self.api.flush(self.j)
            self.log(state+(' · '+reason if reason else ''))
        try:
            self.log('Abriendo el chat…')
            self.gate();self.ui.open_chat(p,message)
            self.gate();self.ui.verify_recipient(p)
            self.log('Chat abierto. Comprobando el mensaje cargado…')
            self.ui.prepare(message)
            self.gate();self.ui.verify_recipient(p);self.ui.verify_content(message)
            record('runner_prepared')
            self.gate()
            if self.api:self.api.call('check',attempt=aid)
            self.ui.verify_recipient(p);self.ui.verify_content(message)
            # Write-ahead: crash/timeout at the next line can never cause an automatic retry.
            actioned=True;record('runner_actioned')
            self.gate();self.ui.verify_recipient(p);self.ui.verify_content(message)
            self.log('Mensaje comprobado. Accionando Enviar…')
            self.ui.send()
            self.ui.verify_sent(message)
            confirmed=True
            record('runner_confirmed_ui')
            if test:self.j.pass_test()
            return True
        except Exception as e:
            if confirmed:
                self.log('Envío comprobado en interfaz y guardado localmente; sincronización pendiente. Detenido.')
                raise
            reason='Detenido por usuario' if isinstance(e,Stopped) else getattr(e,'safe_reason','No se pudo comprobar el estado. Revisa WhatsApp y el diagnóstico local.')
            # Never persist raw exceptions, which can contain full UI text or credentials.
            try:record('runner_uncertain' if actioned else 'runner_failed',reason)
            except Exception:self.log('Resultado guardado localmente; sincronización pendiente. Detenido.')
            raise
