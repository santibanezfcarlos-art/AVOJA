"""Portable sending state machine. Does not import Windows libraries."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

CAMPAIGN_MESSAGE = 'Hola, {nombre} 👋\n\n¡Te hemos echado de menos en Valhalla! 💚 Queremos invitarte a conocer nuestra nueva plataforma, para que organizar tus atenciones sea mucho más fácil.\n\n🌐 https://valhallaoffice.cl\n\n📅 Compra horas y reserva tu box según la disponibilidad.\n\n⏳ Tus horas no vencen: úsalas a tu ritmo.\n\n🔑 Recibe tu código de acceso automáticamente por correo a las 22:00 del día anterior a tu reserva.\n\n🔄 Si cambian tus planes, puedes reagendar tu reserva una vez, con al menos 2 horas de anticipación, incluso para otro horario del mismo día.\n\nSi tienes dudas o necesitas ayuda para comenzar, escríbenos por aquí 😊\n\n¡Te esperamos!\nEquipo Valhalla Office 🌿\n\nSi prefieres no recibir estas novedades, responde BAJA.'

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
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE IF NOT EXISTS test_reviews (id INTEGER PRIMARY KEY,campaign TEXT,phone TEXT,previous_state TEXT,reviewed_at TEXT,source TEXT);''')
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
    def confirm_observed_test(self,p,message=TEST_MESSAGE):
        # This explicit UI action applies only to the fixed self-test, never a campaign.
        p=phone(p);expected=hashlib.sha256(message.encode()).hexdigest()
        with self.db() as c:
            row=c.execute("SELECT campaign,state,message_hash,attempt FROM attempts WHERE campaign LIKE 'prueba-%' AND phone=? ORDER BY rowid DESC LIMIT 1",(p,)).fetchone()
            if not row or row[2]!=expected or row[3]!='test':raise ValueError('No hay una prueba local de este mensaje para ese número.')
            if row[1] not in ('runner_actioned','runner_uncertain','runner_confirmed_ui','test_confirmed_by_user'):
                raise ValueError('La última prueba no llegó a accionar Enviar. No se modifica su registro.')
            if row[1]!='test_confirmed_by_user':
                c.execute('INSERT INTO test_reviews(campaign,phone,previous_state,reviewed_at,source) VALUES(?,?,?,?,?)',
                          (row[0],p,row[1],datetime.now(timezone.utc).isoformat(),'user_observed_sent'))
                c.execute("UPDATE attempts SET state='test_confirmed_by_user',reason='El usuario confirmó el mensaje visible en su conversación; no se reenvió' WHERE campaign=? AND phone=?",(row[0],p))
            c.execute("INSERT OR REPLACE INTO meta VALUES('test_passed','user_observed_sent')")
            return row[0]
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

class ConfirmationPending(Exception):
    safe_reason="Enviar fue accionado; confirmación visual pendiente. No reenviar este número."

class Stopped(Exception): pass

class Runner:
    def __init__(self,journal,adapter,gate,log,api=None):
        self.j=journal;self.ui=adapter;self.gate=gate;self.log=log;self.api=api
    def run_one(self,campaign,item,test=False):
        p=phone(item['phone']);message=item['message']; aid='test'; actioned=False; confirmed=False; send_returned=False
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
            self.gate()
            self.ui.verify_recipient(p);self.ui.verify_content(message)
            record('runner_prepared')
            self.gate()
            if self.api:self.api.call('check',attempt=aid)
            self.ui.verify_recipient(p)
            # Write-ahead: crash/timeout at the next line can never cause an automatic retry.
            actioned=True;record('runner_actioned')
            self.gate();self.ui.verify_recipient(p)
            self.log('Mensaje comprobado. Accionando Enviar…')
            self.ui.send()
            send_returned=True
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
            except Exception:
                self.log('Resultado guardado localmente; sincronización pendiente. Detenido.')
                raise
            if send_returned and isinstance(e,ConfirmationPending) and not test:
                self.log('Número bloqueado para reenvío. Continúa con el siguiente destinatario.')
                return False
            raise
