"""Scoped, expiring desktop-runner access. No WhatsApp sending occurs on server."""
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from whatsapp_campaigns import clean_message

ELIGIBLE = ('pending', 'opened')
STATES = ('runner_claimed','runner_prepared','runner_actioned','runner_confirmed_ui','runner_uncertain','runner_failed')
TRANSITIONS = {
    'runner_claimed': {'runner_prepared','runner_failed','runner_uncertain'},
    'runner_prepared': {'runner_actioned','runner_failed','runner_uncertain'},
    'runner_actioned': {'runner_confirmed_ui','runner_uncertain'},
    'runner_confirmed_ui': set(), 'runner_uncertain': set(), 'runner_failed': set(),
}

def initialize(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS whatsapp_runner_grants (
      digest TEXT PRIMARY KEY, actor INTEGER NOT NULL, campaign_id INTEGER NOT NULL,
      phones TEXT NOT NULL, message_hash TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS whatsapp_runner_attempts (
      campaign_id INTEGER NOT NULL, phone TEXT NOT NULL, attempt TEXT UNIQUE NOT NULL,
      grant_digest TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
      PRIMARY KEY(campaign_id,phone));
    CREATE TABLE IF NOT EXISTS whatsapp_runner_events (
      id INTEGER PRIMARY KEY, attempt TEXT NOT NULL, status TEXT NOT NULL,
      reason TEXT NOT NULL, created_at TEXT NOT NULL);
    ''')

def stamp():
    return datetime.now(timezone.utc).isoformat()

def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def issue(store, actor, body):
    phones = body.get('phones')
    if not isinstance(phones, list) or not 1 <= len(phones) <= 5000 or not all(isinstance(p,str) for p in phones):
        raise ValueError('Selecciona destinatarios para conectar.')
    with store.connection(write=True) as c:
        store.require_owner(c, actor)
        campaign = c.execute('SELECT * FROM whatsapp_campaigns WHERE id=?',(body.get('campaign_id'),)).fetchone()
        if not campaign: raise ValueError('Selecciona una campaña guardada.')
        allowed = []
        for p in dict.fromkeys(phones):
            r = c.execute('''SELECT c.opted_out,COALESCE(p.status,'pending') AS status FROM whatsapp_contacts c
                LEFT JOIN whatsapp_progress p ON p.phone=c.phone AND p.campaign_id=? WHERE c.phone=?''',(campaign['id'],p)).fetchone()
            attempted = c.execute('SELECT 1 FROM whatsapp_runner_attempts WHERE campaign_id=? AND phone=?',(campaign['id'],p)).fetchone()
            if r and not r['opted_out'] and r['status'] in ELIGIBLE and not attempted: allowed.append(p)
        if not allowed: raise ValueError('No hay destinatarios pendientes sin intentos previos en esta selección.')
        token = secrets.token_urlsafe(32)
        c.execute('DELETE FROM whatsapp_runner_grants WHERE expires<?',(time.time(),))
        c.execute('INSERT INTO whatsapp_runner_grants VALUES(?,?,?,?,?,?)',
                  (digest(token),actor,campaign['id'],json.dumps(allowed),digest(campaign['message']),time.time()+28800))
        store.log(c,actor,'whatsapp_runner_connected',campaign['id'])
        return {'token':token,'expires_in':28800,'count':len(allowed),'campaign_id':campaign['id']}

def api(store, token, operation, body):
    if not token or len(token)>200: raise PermissionError('Conexión requerida.')
    with store.connection(write=True) as c:
        grant = c.execute('SELECT * FROM whatsapp_runner_grants WHERE digest=? AND expires>?',(digest(token),time.time())).fetchone()
        if not grant: raise PermissionError('Conexión vencida o inválida. Conecta de nuevo desde Administración.')
        store.require_owner(c,grant['actor'])
        cid=grant['campaign_id']; allowed=set(json.loads(grant['phones']))
        campaign=c.execute('SELECT * FROM whatsapp_campaigns WHERE id=?',(cid,)).fetchone()
        if not campaign or digest(campaign['message'])!=grant['message_hash']:
            raise ValueError('La campaña cambió. Genera una nueva conexión.')
        def eligible(phone):
            r=c.execute('''SELECT c.*,COALESCE(p.status,'pending') AS progress FROM whatsapp_contacts c
                LEFT JOIN whatsapp_progress p ON p.phone=c.phone AND p.campaign_id=? WHERE c.phone=?''',(cid,phone)).fetchone()
            return r if r and not r['opted_out'] and phone in allowed else None
        def attempt():
            r=c.execute('SELECT * FROM whatsapp_runner_attempts WHERE attempt=? AND grant_digest=?',(body.get('attempt'),grant['digest'])).fetchone()
            if not r: raise ValueError('Intento no encontrado para esta conexión.')
            return r
        if operation=='queue':
            records=[]
            for p in sorted(allowed):
                r=eligible(p)
                if r and r['progress'] in ELIGIBLE and not c.execute('SELECT 1 FROM whatsapp_runner_attempts WHERE campaign_id=? AND phone=?',(cid,p)).fetchone():
                    records.append({'phone':p,'name':r['first_name'],'message':clean_message(campaign['message'].replace('{nombre}',r['first_name']))})
            # Fresh counts use exactly the same matching definition as the admin report.
            users={r['email'].strip().lower() for r in c.execute("SELECT email FROM users WHERE role='professional' AND deleted_at=''")}
            contacts=list(c.execute('SELECT emails,opted_out FROM whatsapp_contacts'))
            matched=sum(any(e in users for e in json.loads(r['emails'])) for r in contacts)
            return {'campaign_id':cid,'campaign_name':campaign['name'],'recipients':records,
                    'counts':{'total':len(contacts),'registered':matched,'not_registered':len(contacts)-matched,'opted_out':sum(r['opted_out'] for r in contacts)}}
        if operation=='claim':
            p=body.get('phone'); r=eligible(p)
            if not r or r['progress'] not in ELIGIBLE: raise ValueError('Destinatario excluido o ya procesado.')
            if c.execute('SELECT 1 FROM whatsapp_runner_attempts WHERE campaign_id=? AND phone=?',(cid,p)).fetchone():
                raise ValueError('Ya existe un intento. No se reenvía automáticamente.')
            aid=secrets.token_urlsafe(24); ts=stamp()
            c.execute('INSERT INTO whatsapp_runner_attempts VALUES(?,?,?,?,?,?,?)',(cid,p,aid,grant['digest'],'runner_claimed','',ts))
            c.execute('INSERT INTO whatsapp_runner_events(attempt,status,reason,created_at) VALUES(?,?,?,?)',(aid,'runner_claimed','',ts))
            c.execute('INSERT INTO whatsapp_progress VALUES(?,?,?,?) ON CONFLICT(campaign_id,phone) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at',(cid,p,'runner_claimed',ts))
            return {'attempt':aid,'phone':p,'message':clean_message(campaign['message'].replace('{nombre}',r['first_name']))}
        if operation=='check':
            a=attempt(); r=eligible(a['phone'])
            if not r or r['progress']!=a['status'] or a['status']!='runner_prepared': raise ValueError('El contacto o su estado cambió; no enviar.')
            return {'ok':True}
        if operation=='event':
            a=attempt(); target=body.get('status'); reason=str(body.get('reason') or '')[:300]
            if target==a['status']: return {'ok':True} # Idempotent acknowledgement after network interruption.
            if target not in TRANSITIONS[a['status']]: raise ValueError('Transición de envío inválida.')
            if target in ('runner_prepared','runner_actioned'):
                r=eligible(a['phone'])
                if not r or r['progress']!=a['status']: raise ValueError('El destinatario fue excluido o cambió de estado.')
            ts=stamp()
            c.execute('UPDATE whatsapp_runner_attempts SET status=?,reason=?,updated_at=? WHERE attempt=?',(target,reason,ts,a['attempt']))
            c.execute('INSERT INTO whatsapp_runner_events(attempt,status,reason,created_at) VALUES(?,?,?,?)',(a['attempt'],target,reason,ts))
            # Preserve explicit manual omission/sent/reset decisions during an active attempt.
            c.execute('UPDATE whatsapp_progress SET status=?,updated_at=? WHERE campaign_id=? AND phone=? AND status=?',(target,ts,cid,a['phone'],a['status']))
            store.log(c,grant['actor'],target,a['phone'])
            return {'ok':True}
        raise ValueError('Operación inválida.')
