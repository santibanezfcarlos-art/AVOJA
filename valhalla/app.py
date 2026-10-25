"""Valhalla Office: prototipo local, sin dependencias ni cobros reales."""
import argparse
import base64
import binascii
import csv
import io
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from mailer import EmailDelivery, EmailDeliveryError
from payments import PaymentService, WebpayGateway, PaymentError
from notifications import NotificationDispatcher, booking_links
import google_auth
import microsoft_auth
from rules import purchase_rules, validate_acceptance, RULES_VERSION, RULES_TEXT

ROOT = Path(__file__).resolve().parent
CHILE = ZoneInfo('America/Santiago')
CATALOG = {'1': (1, 5990), '5': (5, 28990), '10': (10, 54990), '30': (30, 144990), '60': (60, 254990), '100': (100, 364990), '200': (200, 599990)}
SESSION_MINUTES = 55
OPENING_HOUR = 7
CLOSING_HOUR = 23
MIN_BOOKING_NOTICE = timedelta(hours=24)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BODY = 14_100_000
ADMIN_EMAIL = "santibanezf.carlos@gmail.com"
DEFAULT_MANAGEMENT_EMAIL = "angelacs7@hotmail.com"
LEGACY_DEMO_EMAILS = ("admin@valhalla.demo", "profesional@valhalla.demo")


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def optional_comment(body,key='note'):
    value=body.get(key,'')
    if value is None:return ''
    if not isinstance(value,str) or len(value.strip())>500:
        raise Problem('El comentario debe ser texto de hasta 500 caracteres.')
    return value.strip()


def supports_professional(user):
    return user and (user['role'] == 'professional' or (user['role'] == 'admin' and user['email'] == ADMIN_EMAIL))


GALLERY_GROUPS = (
    ('entrada', 'Entrada', None, ('1.png',)),
    ('recepcion', 'Recepción', None, ('2.png', '3.png')),
    ('box-infantil', 'Box infantil', 3, ('6.png', '7.png')),
    ('box-1', 'Box 1', 1, ('11.png', '12.png')),
    ('box-2', 'Box 2', 2, ('13.png', '14.png', '15.png')),
)
IMAGE_TYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}


def image_asset(filename):
    allowed = {filename for _, _, _, names in GALLERY_GROUPS for filename in names}
    allowed.add('valhalla-logo.png')
    allowed.update(f'box-{box}.{extension}' for box in (1, 2, 3) for extension in IMAGE_TYPES)
    if filename not in allowed:
        return None
    directory = (ROOT / 'static' / 'images').resolve()
    image = directory / filename
    if image.is_file() and image.resolve().parent == directory:
        return image, IMAGE_TYPES[image.suffix.lstrip('.')]
    return None


def gallery_items():
    groups = []
    for gid, title, box, names in GALLERY_GROUPS:
        photos = []
        for filename in names:
            asset = image_asset(filename)
            if asset:
                photos.append({'image_url': f'/images/{filename}?v={asset[0].stat().st_mtime_ns}',
                               'alt': f'{title} · fotografía {len(photos) + 1}'})
        if not photos and box:
            for extension in IMAGE_TYPES:
                filename = f'box-{box}.{extension}'
                asset = image_asset(filename)
                if asset:
                    photos.append({'image_url': f'/images/{filename}?v={asset[0].stat().st_mtime_ns}', 'alt': title})
                    break
        if photos:
            groups.append({'id': gid, 'title': title, 'box': box, 'photos': photos})
    return groups


def room_photo(box):
    group = next((group for group in gallery_items() if group['box'] == box), None)
    if group:
        filename = group['photos'][0]['image_url'].split('?')[0].rsplit('/', 1)[-1]
        return image_asset(filename)
    return None


def room_photos():
    return [{'box': group['box'], 'title': group['title'], 'image_url': group['photos'][0]['image_url'],
             'photos': group['photos']} for group in sorted((g for g in gallery_items() if g['box']), key=lambda g: g['box'])]


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')


def booking_details(row):
    result = dict(row)
    result['end'] = stamp(datetime.fromisoformat(result['start']) + timedelta(minutes=SESSION_MINUTES))
    result['duration_minutes'] = SESSION_MINUTES
    result['links'] = booking_links(result)
    return result


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return salt + ':' + digest


def password_matches(password, encoded):
    return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS skedu_daily_contacts (
                reference TEXT PRIMARY KEY, booking_date TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL,
                box INTEGER NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, original_status TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1, actor_id INTEGER NOT NULL REFERENCES users(id), updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS skedu_daily_contacts_day ON skedu_daily_contacts(booking_date,active);
            CREATE TABLE IF NOT EXISTS management_grants (
                email TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1,
                actor_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS blocked_emails (
                email TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1,
                actor_id INTEGER NOT NULL REFERENCES users(id), note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS user_access_events (
                id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), email TEXT NOT NULL,
                actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS personal_approvals (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                actor_id INTEGER NOT NULL REFERENCES users(id), approved_name TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, revoked_at TEXT NOT NULL DEFAULT '',
                revoked_by INTEGER REFERENCES users(id));
            CREATE UNIQUE INDEX IF NOT EXISTS one_personal_approval ON personal_approvals(user_id) WHERE revoked_at='';
            CREATE TABLE IF NOT EXISTS discount_codes (
                code TEXT PRIMARY KEY, percent INTEGER NOT NULL CHECK(percent BETWEEN 1 AND 99),
                active INTEGER NOT NULL DEFAULT 1, actor_id INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
              password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'professional',
              specialty TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS email_verifications (
              token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              created_at REAL NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS password_resets (
              token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              created_at REAL NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS recovery_limits (
              email_hash TEXT PRIMARY KEY, requested_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS package_archive_events (
              id INTEGER PRIMARY KEY, package_id INTEGER NOT NULL REFERENCES packages(id),
              actor_id INTEGER NOT NULL REFERENCES users(id), reason TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session_adjustments (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              package_id INTEGER NOT NULL REFERENCES packages(id), actor_id INTEGER NOT NULL REFERENCES users(id),
              delta INTEGER NOT NULL, before_balance INTEGER NOT NULL, after_balance INTEGER NOT NULL,
              reason TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(actor_id,request_key));
            CREATE TABLE IF NOT EXISTS manual_credits (
              id INTEGER PRIMARY KEY, package_id INTEGER NOT NULL UNIQUE REFERENCES packages(id),
              user_id INTEGER NOT NULL REFERENCES users(id), actor_id INTEGER NOT NULL REFERENCES users(id),
              method TEXT NOT NULL, amount INTEGER NOT NULL, reference TEXT NOT NULL, note TEXT NOT NULL,
              request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, received_on TEXT NOT NULL,
              UNIQUE(actor_id,request_key), UNIQUE(method,reference));
            CREATE TABLE IF NOT EXISTS booking_batches (
              user_id INTEGER NOT NULL REFERENCES users(id), request_key TEXT NOT NULL,
              fingerprint TEXT NOT NULL, booking_ids TEXT NOT NULL, created_at TEXT NOT NULL,
              PRIMARY KEY(user_id,request_key));
            CREATE TABLE IF NOT EXISTS booking_rule_acceptances (
                id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id),
                user_id INTEGER NOT NULL REFERENCES users(id), version TEXT NOT NULL,
                rules_text TEXT NOT NULL, accepted_at TEXT NOT NULL, is_modification INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS purchase_acceptances (
              package_id INTEGER PRIMARY KEY REFERENCES packages(id), user_id INTEGER NOT NULL REFERENCES users(id),
              version TEXT NOT NULL, rules_text TEXT NOT NULL, accepted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS webpay_live_settings (
              id INTEGER PRIMARY KEY CHECK(id=1), sales_enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, commerce_code TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS webpay_settings (
              id INTEGER PRIMARY KEY CHECK(id=1), commerce_code TEXT NOT NULL DEFAULT '',
              sandbox_enabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS microsoft_accounts (
              subject TEXT PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), email TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS microsoft_auth_states (
              state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier TEXT NOT NULL,
              expires REAL NOT NULL, link_session TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS google_accounts (
              subject TEXT PRIMARY KEY, user_id INTEGER NOT NULL UNIQUE REFERENCES users(id), email TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS google_auth_states (
              state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL, verifier TEXT NOT NULL,
              expires REAL NOT NULL, link_session TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              filename TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
              reviewer_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS document_attachments (
              document_id INTEGER NOT NULL REFERENCES documents(id), position INTEGER NOT NULL,
              filename TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL,
              PRIMARY KEY(document_id, position));
            CREATE TABLE IF NOT EXISTS packages (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              hours INTEGER NOT NULL, remaining INTEGER NOT NULL CHECK(remaining >= 0),
              price INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS payment_attempts (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              package_id INTEGER NOT NULL UNIQUE REFERENCES packages(id), idempotency_key TEXT NOT NULL,
              buy_order TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL UNIQUE, amount INTEGER NOT NULL,
              hours INTEGER NOT NULL, environment TEXT NOT NULL, state TEXT NOT NULL,
              token TEXT UNIQUE, checkout_url TEXT, processing_until REAL NOT NULL DEFAULT 0, checked_at REAL NOT NULL DEFAULT 0,
              commit_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              UNIQUE(user_id,idempotency_key));
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_webpay_per_user ON payment_attempts(user_id)
              WHERE state IN ('creating','ready','uncertain','review');
            CREATE TABLE IF NOT EXISTS legacy_imports (
              id INTEGER PRIMARY KEY, reference TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL, specialty TEXT NOT NULL, sessions INTEGER NOT NULL CHECK(sessions>=0),
              note TEXT NOT NULL, actor_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
              user_id INTEGER UNIQUE REFERENCES users(id), package_id INTEGER UNIQUE REFERENCES packages(id),
              identity_valid INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS bookings (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              package_id INTEGER NOT NULL REFERENCES packages(id),
              box INTEGER NOT NULL CHECK(box BETWEEN 1 AND 3), start TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'confirmed', modifications INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS package_owner_immutable
              BEFORE UPDATE OF user_id ON packages
              WHEN NEW.user_id <> OLD.user_id
              BEGIN SELECT RAISE(ABORT, 'Package ownership cannot be transferred'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS occupied_slot ON bookings(box, start)
              WHERE status = 'confirmed';
            CREATE TABLE IF NOT EXISTS mail_events (
              id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL,
              provider TEXT NOT NULL, status TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
              http_status INTEGER, created_at TEXT NOT NULL, created_epoch REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS notification_outbox (
              id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, delivery_key TEXT NOT NULL UNIQUE,
              user_id INTEGER NOT NULL REFERENCES users(id), recipient TEXT NOT NULL, kind TEXT NOT NULL,
              entity_id INTEGER NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0, first_attempt REAL NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
              lease_until REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
              action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')
            if 'received_on' not in {row['name'] for row in c.execute('PRAGMA table_info(manual_credits)')}:
                c.execute("ALTER TABLE manual_credits ADD COLUMN received_on TEXT NOT NULL DEFAULT ''")
                for record in c.execute('SELECT id,created_at FROM manual_credits').fetchall():
                    c.execute('UPDATE manual_credits SET received_on=? WHERE id=?',(datetime.fromisoformat(record['created_at']).astimezone(CHILE).date().isoformat(),record['id']))
            notification_columns = {row['name'] for row in c.execute('PRAGMA table_info(notification_outbox)')}
            if 'first_attempt' not in notification_columns:
                c.execute('ALTER TABLE notification_outbox ADD COLUMN first_attempt REAL NOT NULL DEFAULT 0')
            columns = {row['name'] for row in c.execute('PRAGMA table_info(documents)')}
            user_columns = {row['name'] for row in c.execute('PRAGMA table_info(users)')}
            if 'email_verified' not in user_columns:
                c.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0')
            if 'commerce_code' not in {row['name'] for row in c.execute('PRAGMA table_info(webpay_live_settings)')}:
                c.execute("ALTER TABLE webpay_live_settings ADD COLUMN commerce_code TEXT NOT NULL DEFAULT ''")
            if 'deleted_at' not in {row['name'] for row in c.execute('PRAGMA table_info(users)')}:
                c.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")
            payment_columns = {row['name'] for row in c.execute('PRAGMA table_info(payment_attempts)')}
            for column in ('checked_at','commit_requested'):
                if column not in payment_columns:
                    c.execute(f'ALTER TABLE payment_attempts ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
            if 'commerce_code' not in payment_columns:
                c.execute("ALTER TABLE payment_attempts ADD COLUMN commerce_code TEXT NOT NULL DEFAULT ''")
            if 'approved_at' not in payment_columns:
                c.execute("ALTER TABLE payment_attempts ADD COLUMN approved_at TEXT NOT NULL DEFAULT ''")
            if 'archived' not in payment_columns:
                c.execute('ALTER TABLE payment_attempts ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            index_sql=c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='one_open_webpay_per_user'").fetchone()
            if index_sql and ('archived' not in index_sql['sql'] or 'user_id,environment' not in index_sql['sql'].replace(' ','')):
                c.execute('DROP INDEX one_open_webpay_per_user')
                c.execute("CREATE UNIQUE INDEX one_open_webpay_per_user ON payment_attempts(user_id,environment) WHERE state IN ('creating','ready','uncertain','review') AND archived=0")
            package_columns = {row['name'] for row in c.execute('PRAGMA table_info(packages)')}
            for column,definition in [('archived_at',"TEXT NOT NULL DEFAULT ''"),('archive_reason',"TEXT NOT NULL DEFAULT ''"),('archived_by','INTEGER REFERENCES users(id)')]:
                if column not in package_columns:c.execute(f'ALTER TABLE packages ADD COLUMN {column} {definition}')
            for column,definition in [('discount_code',"TEXT NOT NULL DEFAULT ''"),('discount_percent','INTEGER NOT NULL DEFAULT 0'),('original_price','INTEGER NOT NULL DEFAULT 0')]:
                if column not in package_columns:c.execute(f'ALTER TABLE packages ADD COLUMN {column} {definition}')
            if 'source' not in package_columns:
                c.execute("ALTER TABLE packages ADD COLUMN source TEXT NOT NULL DEFAULT 'simulated'")
            if 'kind' not in columns:
                c.execute("ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'accreditation'")

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def log(self, c, actor, action, target):
        c.execute('INSERT INTO audit(actor_id,action,target,created_at) VALUES (?,?,?,?)',
                  (actor, action, str(target), stamp(now())))

    def queue_notification(self, c, uid, kind, entity_id, recipient, payload, event_key=None):
        event_key = event_key or f'{kind}:{entity_id}'
        c.execute('INSERT OR IGNORE INTO notification_outbox(event_key,delivery_key,user_id,recipient,kind,entity_id,payload,created_at) VALUES(?,?,?,?,?,?,?,?)',
                  (event_key,secrets.token_hex(24),uid,recipient,kind,entity_id,json.dumps(payload),stamp(now())))

    def queue_hours_receipt(self, c, uid, pid, source):
        package=c.execute("SELECT hours FROM packages WHERE id=? AND user_id=? AND status='paid'",(pid,uid)).fetchone()
        if not package or package['hours']<=0:return
        person=c.execute('SELECT name,email FROM users WHERE id=?',(uid,)).fetchone()
        remaining=c.execute("SELECT COALESCE(SUM(remaining),0) FROM packages WHERE user_id=? AND status='paid' AND archived_at=''",(uid,)).fetchone()[0]
        self.queue_notification(c,uid,'hours_credited',pid,person['email'],dict(person)|{'hours':package['hours'],'remaining':remaining,'source':source})

    def queue_booking_receipt(self, c, uid, bid, kind, remaining=None):
        person = c.execute('SELECT name,email FROM users WHERE id=?',(uid,)).fetchone()
        booking = c.execute('SELECT id,box,start,status FROM bookings WHERE id=? AND user_id=?',(bid,uid)).fetchone()
        c.execute("UPDATE notification_outbox SET status='superseded' WHERE entity_id=? AND kind IN ('booking_confirmed','booking_modified') AND status IN ('pending','retry','processing')",(bid,))
        payload = dict(person);payload['booking'] = dict(booking)
        payload['remaining']=remaining if remaining is not None else c.execute("SELECT COALESCE(SUM(remaining),0) FROM packages WHERE user_id=? AND status='paid'",(uid,)).fetchone()[0]
        self.queue_notification(c,uid,kind,bid,person['email'],payload)

    def daily_contacts(self, date):
        try:
            day = datetime.strptime(date,'%Y-%m-%d').date()
        except (ValueError,TypeError):
            raise Problem('Selecciona una fecha válida.')
        start = datetime(day.year,day.month,day.day,tzinfo=CHILE)
        try:
            next_day = day + timedelta(days=1)
            end = datetime(next_day.year,next_day.month,next_day.day,tzinfo=CHILE)
        except (ValueError,OverflowError):
            raise Problem('Selecciona una fecha válida.')
        with self.connection() as c:
            rows = c.execute("""SELECT b.id,b.user_id,b.box,b.start,u.name,u.email FROM bookings b
                JOIN users u ON u.id=b.user_id WHERE b.status='confirmed' AND b.start>=? AND b.start<?
                ORDER BY b.start,b.box,b.id""",(stamp(start),stamp(end))).fetchall()
            external=c.execute('SELECT * FROM skedu_daily_contacts WHERE booking_date=? AND active=1 ORDER BY start,box,reference',(date,)).fetchall()
        contacts = {}
        for row in rows:
            email=row['email'].strip().lower()
            person = contacts.setdefault(email,{'user_id':row['user_id'],'name':row['name'],'email':email,'bookings':[],'skedu_bookings':[]})
            person['bookings'].append(booking_details({'id':row['id'],'box':row['box'],'start':row['start']}))
        for row in external:
            email=row['email'].strip().lower()
            person=contacts.setdefault(email,{'user_id':None,'name':row['name'],'email':email,'bookings':[],'skedu_bookings':[]})
            person['skedu_bookings'].append({key:row[key] for key in ('reference','box','start','end','original_status')})
        people = sorted(contacts.values(),key=lambda person:(person['name'].casefold(),person['email']))
        return {'date':date,'timezone':'America/Santiago','contacts':people,'emails':[person['email'] for person in people],
                'total_professionals':len(people),'total_bookings':len(rows),'total_skedu_bookings':len(external)}

    def import_skedu_contacts(self,actor,body,preview=False):
        from skedu_contacts import read_skedu_contacts
        with self.connection() as c:self.require_owner(c,actor)
        if type(body.get('include_unconfirmed',False)) is not bool:
            raise Problem('Indica si las reservas no confirmadas siguen vigentes.')
        if not preview and body.get('confirmed') is not True:
            raise Problem('Confirma la incorporación al listado y al envío automático del código de acceso.')
        try:records,metadata=read_skedu_contacts(body.get('content'),body.get('include_unconfirmed',False))
        except Exception as error:
            if isinstance(error,ValueError):raise Problem(str(error)) from error
            raise Problem('No se pudo leer este Excel. Usa el archivo XLSX original de Skedu.') from error
        days={}
        for row in records:
            if not row['active']:continue
            summary=days.setdefault(row['date'],{'date':row['date'],'bookings':0,'emails':set()})
            summary['bookings']+=1;summary['emails'].add(row['email'])
        with self.connection(True) as c:
            self.require_owner(c,actor)
            if not preview:
                for row in records:
                    c.execute('''INSERT INTO skedu_daily_contacts(reference,booking_date,name,email,box,start,end,original_status,active,actor_id,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(reference) DO UPDATE SET booking_date=excluded.booking_date,name=excluded.name,
                        email=excluded.email,box=excluded.box,start=excluded.start,end=excluded.end,original_status=excluded.original_status,
                        active=excluded.active,actor_id=excluded.actor_id,updated_at=excluded.updated_at''',
                        (row['reference'],row['date'],row['name'],row['email'],row['box'],row['start'],row['end'],row['original_status'],row['active'],actor,stamp(now())))
                self.log(c,actor,'skedu_contacts_imported',len(records))
        return dict(metadata,preview=preview,total_bookings=sum(day['bookings'] for day in days.values()),
                    days=[{'date':day['date'],'bookings':day['bookings'],'contacts':len(day['emails'])} for day in sorted(days.values(),key=lambda day:day['date'])])

    def remove_skedu_contacts(self,actor,body):
        references=body.get('references')
        if body.get('confirmed') is not True or not isinstance(references,list) or not 1<=len(references)<=5000 or any(not isinstance(ref,str) or len(ref)>200 for ref in references):
            raise Problem('Confirma qué reservas de Skedu deseas quitar del listado y de futuros envíos de acceso.')
        with self.connection(True) as c:
            self.require_owner(c,actor)
            for reference in references:
                c.execute('UPDATE skedu_daily_contacts SET active=0,actor_id=?,updated_at=? WHERE reference=?',(actor,stamp(now()),reference))
            self.log(c,actor,'skedu_contacts_removed',len(references))
        return {'ok':True}

    def mail_event(self, uid, kind, provider, status, category='', http_status=None):
        with self.connection(True) as c:
            if kind == 'test':
                previous = c.execute("SELECT MAX(created_epoch) FROM mail_events WHERE kind='test'").fetchone()[0]
                if previous is not None and time.time()-previous < 60:
                    raise Problem('Espera un minuto antes de enviar otra prueba.',429)
            # No recipient supplied by an anonymous request, provider body, token or secret is stored.
            return c.execute('INSERT INTO mail_events(user_id,kind,provider,status,category,http_status,created_at,created_epoch) VALUES(?,?,?,?,?,?,?,?)',
                             (uid,kind,provider,status,category,http_status,stamp(now()),time.time())).lastrowid

    def mail_outcome(self, eid, status, category='', http_status=None):
        with self.connection(True) as c:
            c.execute('UPDATE mail_events SET status=?,category=?,http_status=? WHERE id=?',(status,category,http_status,eid))

    def configure_access(self, admin_password=None):
        """Retire public demo credentials and provision the owner only from a private setting."""
        if admin_password is not None and not 14 <= len(admin_password) <= 200:
            raise ValueError('VALHALLA_ADMIN_PASSWORD debe tener entre 14 y 200 caracteres.')
        with self.connection(True) as c:
            blocked = c.execute("SELECT id FROM users WHERE email IN (?,?) OR (role='admin' AND email<>?)",
                                (*LEGACY_DEMO_EMAILS, ADMIN_EMAIL)).fetchall()
            for row in blocked:
                c.execute('UPDATE users SET active=0 WHERE id=?', (row['id'],))
                c.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
            if admin_password:
                owner = c.execute('SELECT * FROM users WHERE email=?', (ADMIN_EMAIL,)).fetchone()
                if owner:
                    # Never promote a public registration while retaining its old password/session.
                    if owner['role'] != 'admin' or not password_matches(admin_password, owner['password']):
                        c.execute('DELETE FROM sessions WHERE user_id=?', (owner['id'],))
                        c.execute("UPDATE users SET password=?,role='admin',active=1 WHERE id=?",
                                  (password_hash(admin_password), owner['id']))
                else:
                    c.execute("INSERT INTO users(name,email,password,role,verified) VALUES(?,?,?,'admin',0)",
                              ('Administración Valhalla', ADMIN_EMAIL, password_hash(admin_password)))

            c.execute("UPDATE users SET email_verified=1 WHERE email=? AND role='admin'", (ADMIN_EMAIL,))
            owner=c.execute("SELECT id FROM users WHERE email=? AND role='admin' AND active=1",(ADMIN_EMAIL,)).fetchone()
            if owner:
                # Initial grant requested by the owner; an explicit revocation survives every restart.
                c.execute('INSERT OR IGNORE INTO management_grants(email,actor_id,created_at) VALUES(?,?,?)',(DEFAULT_MANAGEMENT_EMAIL,owner['id'],stamp(now())))
            # Cached flags never replace approval of both required documents.
            for row in c.execute('SELECT id FROM users').fetchall():
                c.execute('UPDATE users SET verified=? WHERE id=?',(int(self.professional_verified(c,row['id'])),row['id']))

    def management_access(self,c,uid):
        user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not user or not user['active'] or user['deleted_at'] or self.email_blocked(c,user['email']):return False
        if user['role']=='admin' and user['email']==ADMIN_EMAIL:return True
        return bool(user['role']=='professional' and user['email_verified'] and c.execute('SELECT 1 FROM management_grants WHERE email=? AND active=1',(user['email'],)).fetchone())

    def require_management(self,c,actor):
        if not self.management_access(c,actor):raise Problem('Acceso reservado a administración y gestión autorizada.',403)

    def set_management_access(self,actor,body):
        uid,enabled=body.get('user_id'),body.get('enabled')
        if type(uid) is not int or type(enabled) is not bool or body.get('confirmed') is not True:
            raise Problem('Confirma el cambio de acceso de gestión.')
        with self.connection(True) as c:
            self.require_owner(c,actor)
            person=c.execute("SELECT * FROM users WHERE id=? AND role='professional' AND deleted_at=''",(uid,)).fetchone()
            if not person:raise Problem('Selecciona una cuenta profesional existente.',404)
            c.execute('INSERT INTO management_grants(email,active,actor_id,created_at) VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET active=excluded.active,actor_id=excluded.actor_id,created_at=excluded.created_at',(person['email'],int(enabled),actor,stamp(now())))
            self.log(c,actor,'management_access_granted' if enabled else 'management_access_revoked',uid)
        return {'ok':True}

    def personally_approved(self,c,uid):
        return bool(c.execute("SELECT 1 FROM personal_approvals p JOIN users u ON u.id=p.user_id WHERE p.user_id=? AND p.revoked_at='' AND p.approved_name=u.name",(uid,)).fetchone())

    def identity_verified(self, c, uid):
        if self.can_book_parallel(c,uid):return True
        if self.personally_approved(c,uid):return True
        if c.execute('SELECT 1 FROM legacy_imports WHERE user_id=? AND identity_valid=1', (uid,)).fetchone():
            return True
        row = c.execute("SELECT status FROM documents WHERE user_id=? AND kind='identity' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        return bool(row and row['status'] == 'approved')

    def professional_verified(self, c, uid):
        if self.can_book_parallel(c,uid):return True
        if self.personally_approved(c,uid):return True
        if c.execute('SELECT 1 FROM legacy_imports WHERE user_id=? AND identity_valid=1',(uid,)).fetchone():
            return True
        degree=c.execute("SELECT status FROM documents WHERE user_id=? AND kind='degree' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
        return self.identity_verified(c,uid) and bool(degree and degree['status']=='approved')

    def record_purchase_acceptance(self, c, uid, pid, acceptance):
        if not validate_acceptance(acceptance):
            raise Problem('Debes leer y aceptar las normas vigentes antes de comprar.',400)
        c.execute('INSERT INTO purchase_acceptances VALUES(?,?,?,?,?)',(pid,uid,RULES_VERSION,RULES_TEXT,stamp(now())))

    def email_blocked(self,c,email):
        return bool(c.execute('SELECT 1 FROM blocked_emails WHERE email=? AND active=1',(email.strip().lower(),)).fetchone())

    def manage_user_access(self,actor,body):
        action=body.get('action');uid=body.get('user_id');note=optional_comment(body)
        if action not in ('block','unblock','delete') or body.get('confirmed') is not True:raise Problem('Confirma la acción sobre la cuenta.')
        with self.connection(True) as c:
            self.require_owner(c,actor)
            if action=='unblock':
                email=body.get('email','')
                if not isinstance(email,str):raise Problem('Correo inválido.')
                email=email.strip().lower()
                if email==ADMIN_EMAIL:raise Problem('La cuenta administradora está protegida.',403)
                entry=c.execute('SELECT * FROM blocked_emails WHERE email=? AND active=1',(email,)).fetchone()
                if not entry:raise Problem('El correo no tiene un bloqueo activo.',404)
                user=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone();uid=user['id'] if user else None
                c.execute('UPDATE blocked_emails SET active=0 WHERE email=?',(email,))
                history=False
            else:
                if type(uid) is not int:raise Problem('Selecciona una cuenta profesional.')
                user=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                if not user or user['role']!='professional' or user['email']==ADMIN_EMAIL:raise Problem('Solo puedes gestionar cuentas profesionales.',403)
                if user['deleted_at']:raise Problem('Esta cuenta ya fue eliminada.',409)
                email=user['email'];history=False
                if action=='block':
                    c.execute('INSERT INTO blocked_emails(email,active,actor_id,note,created_at) VALUES(?,1,?,?,?) ON CONFLICT(email) DO UPDATE SET active=1,actor_id=excluded.actor_id,note=excluded.note,created_at=excluded.created_at',(email,actor,note,stamp(now())))
                else:
                    history=any(c.execute('SELECT 1 FROM '+table+' WHERE user_id=? LIMIT 1',(uid,)).fetchone() for table in ('packages','bookings','payment_attempts','manual_credits','session_adjustments','legacy_imports'))
                    c.execute('UPDATE users SET active=0,verified=0,deleted_at=?,password=? WHERE id=?',(stamp(now()),password_hash(secrets.token_urlsafe(48)),uid))
                    c.execute("UPDATE personal_approvals SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at=''",(stamp(now()),actor,uid))
                    if not history:
                        # Retain a retired ID for immutable audit links; release an erroneous email.
                        c.execute("UPDATE users SET name='Cuenta eliminada',email=?,specialty='',email_verified=0 WHERE id=?",('deleted-'+secrets.token_hex(16)+'@retired.invalid',uid))
                        c.execute('DELETE FROM document_attachments WHERE document_id IN (SELECT id FROM documents WHERE user_id=?)',(uid,))
                        c.execute('DELETE FROM documents WHERE user_id=?',(uid,))
                    c.execute('DELETE FROM google_accounts WHERE user_id=?',(uid,))
                    c.execute('DELETE FROM microsoft_accounts WHERE user_id=?',(uid,))
                    c.execute('UPDATE management_grants SET active=0,actor_id=?,created_at=? WHERE email=?',(actor,stamp(now()),email))
                    c.execute("UPDATE notification_outbox SET status='superseded' WHERE user_id=? AND status IN ('pending','retry','processing')",(uid,))
                for table in ('sessions','email_verifications','password_resets'):
                    c.execute('DELETE FROM '+table+' WHERE user_id=?',(uid,))
            c.execute('INSERT INTO user_access_events(user_id,email,actor_id,action,note,created_at) VALUES(?,?,?,?,?,?)',(uid,email,actor,action,note,stamp(now())))
            self.log(c,actor,'user_access_'+action,uid or email)
        return {'action':action,'history_preserved':bool(history)}

    def session(self, token):
        with self.connection() as c:
            row = c.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',
                            (token, time.time())).fetchone()
            if not row or not row['active'] or row['deleted_at'] or self.email_blocked(c,row['email']) or row['email'] in LEGACY_DEMO_EMAILS or (row['role'] == 'admin' and row['email'] != ADMIN_EMAIL):
                raise Problem('Inicia sesión para continuar.', 401)
            return dict(row)

    def login(self, body):
        email = str(body.get('email', '')).strip().lower()
        password = str(body.get('password', ''))
        if len(email) > 200 or len(password) > 200:
            raise Problem('Correo o contraseña incorrectos.', 401)
        with self.connection(True) as c:
            row = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            # Work comparable to a real password check for unknown users.
            encoded = row['password'] if row else password_hash('invalid', '0' * 32)
            if not password_matches(password, encoded) or not row or not row['active'] or row['deleted_at'] or self.email_blocked(c,row['email']) or row['email'] in LEGACY_DEMO_EMAILS or (row['role'] == 'admin' and row['email'] != ADMIN_EMAIL):
                raise Problem('Correo o contraseña incorrectos, o cuenta desactivada.', 401)
            if body.get('portal') == 'admin' and not self.management_access(c,row['id']):
                raise Problem('Este acceso está reservado para la cuenta administradora autorizada.', 403)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            c.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
            c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token, row['id'], csrf, time.time() + 28800))
            return token

    def register(self, body):
        name, email = str(body.get('name', '')).strip(), str(body.get('email', '')).strip().lower()
        password, specialty = str(body.get('password', '')), str(body.get('specialty', '')).strip()
        if email == ADMIN_EMAIL or email in LEGACY_DEMO_EMAILS:
            raise Problem('Este correo está reservado y no admite registro profesional.', 403)
        if not 2 <= len(name) <= 100 or len(email) > 200 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise Problem('Ingresa un nombre y un correo válidos.')
        if not 10 <= len(password) <= 200:
            raise Problem('La contraseña debe tener entre 10 y 200 caracteres.')
        if not 2 <= len(specialty) <= 100:
            raise Problem('Indica tu profesión o especialidad.')
        try:
            with self.connection(True) as c:
                if self.email_blocked(c,email):raise Problem('Este correo no tiene permitido registrarse. Contacta a administración.',403)
                cursor = c.execute('INSERT INTO users(name,email,password,specialty) VALUES(?,?,?,?)',
                                   (name, email, password_hash(password), specialty))
                self.log(c, cursor.lastrowid, 'registered', cursor.lastrowid)
                uid = cursor.lastrowid
                self.attach_legacy(c, uid, email)
                self.queue_notification(c,uid,'admin_signup',uid,ADMIN_EMAIL,{'name':name,'email':email,'specialty':specialty})
        except sqlite3.IntegrityError:
            raise Problem('Ya existe una cuenta con ese correo.', 409)
        return uid

    def attach_legacy(self, c, uid, email):
        legacy = c.execute('SELECT * FROM legacy_imports WHERE email=? AND user_id IS NULL', (email,)).fetchone()
        if not legacy:
            return
        package = None
        if legacy['sessions']:
            package = c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at,source) VALUES(?,?,?,0,'paid',?,'migration')", (uid, legacy['sessions'], legacy['sessions'], stamp(now()))).lastrowid
        c.execute('UPDATE legacy_imports SET user_id=?,package_id=? WHERE id=? AND user_id IS NULL', (uid, package, legacy['id']))
        c.execute('UPDATE users SET verified=1 WHERE id=?', (uid,))
        self.log(c, legacy['actor_id'], 'legacy_balance_attached', legacy['id'])

    def import_legacy(self, actor, body, connection=None):
        email = str(body.get('email', '')).strip().lower()
        name = str(body.get('name', '')).strip()
        specialty = str(body.get('specialty', '')).strip()
        reference = str(body.get('reference', '')).strip()
        note = optional_comment(body)
        sessions = body.get('sessions')
        if body.get('identity_confirmed') is not True:
            raise Problem('Confirma que este cliente ya fue validado en el sistema anterior.')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email)>200 or email == ADMIN_EMAIL or email in LEGACY_DEMO_EMAILS:
            raise Problem('Indica el correo de un cliente profesional válido.')
        if not 2<=len(name)<=100 or (specialty and not 2<=len(specialty)<=100) or not 2<=len(reference)<=100 :
            raise Problem('Completa nombre y referencia del sistema anterior; si indicas especialidad, usa entre 2 y 100 caracteres.')
        if type(sessions) is not int or not 0<=sessions<=10000:
            raise Problem('Indica un saldo entero entre 0 y 10.000 sesiones.')
        with (nullcontext(connection) if connection is not None else self.connection(True)) as c:
            administrator = c.execute('SELECT * FROM users WHERE id=?', (actor,)).fetchone()
            if not administrator or administrator['email'] != ADMIN_EMAIL or administrator['role'] != 'admin' or not administrator['active']:
                raise Problem('Acceso reservado a administración.', 403)
            if c.execute('SELECT id FROM legacy_imports WHERE email=? OR reference=?', (email,reference)).fetchone():
                raise Problem('Este correo o referencia ya fue migrado. No se volvió a cargar saldo.', 409)
            existing = c.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            if existing and (existing['role']!='professional' or not existing['active']):
                raise Problem('La cuenta existente debe ser profesional y estar activa.', 409)
            if existing and existing['name'].casefold() != name.casefold():
                raise Problem('El nombre no coincide con la cuenta existente. Verifica el titular antes de migrar.', 409)
            iid = c.execute('INSERT INTO legacy_imports(reference,email,name,specialty,sessions,note,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?)', (reference,email,name,specialty,sessions,note,actor,stamp(now()))).lastrowid
            if existing:
                self.attach_legacy(c, existing['id'], email)
            self.log(c, actor, 'legacy_import_validated', iid)
            return iid

    def import_legacy_csv(self, actor, body, preview=False):
        text = body.get('csv')
        if not isinstance(text, str) or not text.strip() or len(text.encode())>1_000_000:
            raise Problem('Adjunta un CSV con contenido, hasta 1 MB.')
        if body.get('identity_confirmed') is not True:
            raise Problem('Confirma que los clientes de esta planilla ya fueron validados.')
        try:
            delimiter = ';' if text.splitlines()[0].count(';')>text.splitlines()[0].count(',') else ','
            reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), delimiter=delimiter, strict=True)
            expected = ['referencia','nombre','correo','especialidad','sesiones','observacion']
            if reader.fieldnames not in (expected,expected[:-1]):
                raise Problem('Las columnas deben ser: referencia,nombre,correo,especialidad,sesiones. La columna observacion es opcional.')
            rows = list(reader)
        except (csv.Error, IndexError):
            raise Problem('El CSV no tiene un formato válido.')
        if not 1<=len(rows)<=500:
            raise Problem('Importa entre 1 y 500 clientes por archivo.')
        summary = []
        with self.connection(True) as c:
            for number, row in enumerate(rows, 2):
                try:
                    if None in row or any(row.get(key) is None for key in expected[:-1]) or not re.fullmatch(r'\d{1,5}',row['sesiones'].strip()):
                        raise Problem('Verifica el número de columnas y el saldo entero de sesiones.')
                    data = dict(zip(('reference','name','email','specialty','sessions','note'), (row.get(key) for key in expected)))
                    data['sessions'] = int(data['sessions'])
                    data['identity_confirmed'] = True
                    self.import_legacy(actor, data, connection=c)
                    summary.append({'name':data['name'].strip(), 'email':data['email'].strip().lower(), 'sessions':data['sessions'], 'reference':data['reference'].strip()})
                except Problem as error:
                    raise Problem(f'Fila {number}: {error.message} No se importó ningún cliente.', error.status)
            if preview:
                # Reuse every real constraint while rolling back rows, balances and audit entries.
                c.rollback()
        return {'rows':summary, 'count':len(summary), 'sessions':sum(row['sessions'] for row in summary), 'preview':preview}

    def issue_email_token(self, uid):
        token = secrets.token_urlsafe(32)
        with self.connection(True) as c:
            user = c.execute("SELECT email,email_verified FROM users WHERE id=? AND active=1 AND deleted_at=''", (uid,)).fetchone()
            if not user or self.email_blocked(c,user['email']):
                raise Problem('Cuenta no encontrada.', 404)
            if user['email_verified']:
                return None, user['email']
            previous = c.execute('SELECT MAX(created_at) FROM email_verifications WHERE user_id=?', (uid,)).fetchone()[0]
            if previous is not None and time.time()-previous < 60:
                raise Problem('Espera un minuto antes de solicitar otro correo.', 429)
            c.execute('DELETE FROM email_verifications WHERE user_id=?', (uid,))
            c.execute('INSERT INTO email_verifications(token_hash,user_id,created_at,expires) VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, time.time(), time.time()+86400))
        return token, user['email']

    def confirm_email(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise Problem('El enlace de confirmación es inválido o venció.')
        with self.connection(True) as c:
            row = c.execute('SELECT * FROM email_verifications WHERE token_hash=? AND consumed=0 AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise Problem('El enlace de confirmación es inválido, ya se utilizó o venció.')
            c.execute('UPDATE email_verifications SET consumed=1 WHERE token_hash=?', (row['token_hash'],))
            c.execute('UPDATE users SET email_verified=1 WHERE id=?', (row['user_id'],))
            self.log(c, row['user_id'], 'email_verified', row['user_id'])

    def issue_password_reset(self, email):
        email = str(email).strip().lower()
        if len(email) > 200:
            return None
        timestamp = time.time()
        email_hash = hashlib.sha256(email.encode()).hexdigest()
        with self.connection(True) as c:
            previous = c.execute('SELECT requested_at FROM recovery_limits WHERE email_hash=?', (email_hash,)).fetchone()
            if previous and timestamp - previous[0] < 60:
                return None
            c.execute('DELETE FROM recovery_limits WHERE requested_at<?', (timestamp-3600,))
            c.execute('INSERT OR REPLACE INTO recovery_limits VALUES(?,?)', (email_hash, timestamp))
            user = c.execute("SELECT id,email FROM users WHERE email=? AND active=1 AND role='professional'", (email,)).fetchone()
            if user and self.email_blocked(c,email):user=None
            # The administrator password remains managed by the private Render setting.
            if not user or email in LEGACY_DEMO_EMAILS:
                return None
            token = secrets.token_urlsafe(32)
            c.execute('DELETE FROM password_resets WHERE user_id=? OR expires<=?', (user['id'], timestamp))
            c.execute('INSERT INTO password_resets VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), user['id'], timestamp, timestamp+1800))
            return token, user['email']

    def reset_password(self, body):
        token, password = body.get('token'), body.get('password')
        if not isinstance(password, str) or not 10 <= len(password) <= 200:
            raise Problem('La contraseña debe tener entre 10 y 200 caracteres.')
        if password != body.get('password_confirmation'):
            raise Problem('Las contraseñas no coinciden.')
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise Problem('El enlace de recuperación es inválido o venció.')
        with self.connection(True) as c:
            row = c.execute("SELECT r.user_id FROM password_resets r JOIN users u ON u.id=r.user_id WHERE r.token_hash=? AND r.expires>? AND u.active=1 AND u.role='professional'", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if not row:
                raise Problem('El enlace de recuperación es inválido, ya se utilizó o venció.')
            c.execute('UPDATE users SET password=? WHERE id=?', (password_hash(password), row['user_id']))
            c.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
            c.execute('DELETE FROM password_resets WHERE user_id=?', (row['user_id'],))
            self.log(c, row['user_id'], 'password_reset', row['user_id'])

    def document_metadata(self, c, row):
        document = dict(row)
        document['files'] = [{'position': 1, 'filename': document['filename'],
                              'download_url': f"/api/documents/{document['id']}"}]
        document['files'] += [{'position': file['position'], 'filename': file['filename'],
                               'download_url': f"/api/documents/{document['id']}/files/{file['position']}"}
                              for file in c.execute('SELECT position,filename FROM document_attachments WHERE document_id=? ORDER BY position', (document['id'],))]
        return document

    def state(self, user):
        with self.connection() as c:
            profile = {k: user[k] for k in ('id', 'name', 'email', 'role', 'specialty', 'verified', 'active', 'email_verified')}
            profile['management_access'] = self.management_access(c,user['id'])
            profile['profile_required'] = user['role']=='professional' and not 2<=len(user['specialty'].strip())<=100
            profile['microsoft_linked'] = bool(c.execute('SELECT 1 FROM microsoft_accounts WHERE user_id=?',(user['id'],)).fetchone())
            profile['google_linked'] = bool(c.execute('SELECT 1 FROM google_accounts WHERE user_id=?',(user['id'],)).fetchone())
            profile['verified'] = int(self.professional_verified(c, user['id']))
            profile['identity_approved'] = self.identity_verified(c,user['id'])
            degree=c.execute("SELECT status FROM documents WHERE user_id=? AND kind='degree' ORDER BY id DESC LIMIT 1",(user['id'],)).fetchone()
            profile['degree_approved'] = bool(degree and degree['status']=='approved')
            profile['owner_booking_privileges']=self.can_book_parallel(c,user['id'])
            profile['identity_source'] = 'administrator' if profile['owner_booking_privileges'] else 'personal' if self.personally_approved(c,user['id']) else 'legacy' if c.execute('SELECT 1 FROM legacy_imports WHERE user_id=? AND identity_valid=1', (user['id'],)).fetchone() else 'document'
            return {'purchase_rules':purchase_rules(), 'user': profile, 'rooms': room_photos(), 'csrf': user['csrf'], 'today': now().astimezone(CHILE).date().isoformat(), 'earliest_booking_date':(now().astimezone(CHILE).date() if profile['owner_booking_privileges'] else max(now().astimezone(CHILE).date()+timedelta(days=1),(now().astimezone(timezone.utc)+MIN_BOOKING_NOTICE).astimezone(CHILE).date())).isoformat(),
                    'packages': [dict(r) for r in c.execute('SELECT p.*,COALESCE((SELECT SUM(delta) FROM session_adjustments j WHERE j.package_id=p.id),0) AS balance_adjustment,EXISTS(SELECT 1 FROM purchase_acceptances a WHERE a.package_id=p.id) AS rules_accepted,m.method AS manual_method FROM packages p LEFT JOIN manual_credits m ON m.package_id=p.id WHERE p.user_id=? AND p.archived_at=\'\' ORDER BY p.id DESC', (user['id'],))],
                    'bookings': [booking_details(r) for r in c.execute('SELECT * FROM bookings WHERE user_id=? ORDER BY start', (user['id'],))],
                    'documents': [self.document_metadata(c,r) for r in c.execute('SELECT id,filename,status,note,created_at,kind FROM documents WHERE user_id=? ORDER BY id DESC', (user['id'],))],
                    'email_verification_delivery': next((row['status'] for row in c.execute("SELECT status FROM mail_events WHERE user_id=? AND kind='verification' ORDER BY id DESC LIMIT 1",(user['id'],))),None),
                    'payments': [dict(r) for r in c.execute('SELECT id,package_id,buy_order,amount,hours,environment,state,created_at FROM payment_attempts WHERE user_id=? AND archived=0 ORDER BY id DESC', (user['id'],))],
                    'session_minutes': SESSION_MINUTES, 'booking_interval_minutes': 60,
                    'catalog': [{'id': key, 'hours': val[0], 'sessions': val[0], 'duration_minutes': SESSION_MINUTES, 'price': val[1]} for key, val in CATALOG.items()]}

    def update_profile(self, uid, body):
        name = str(body.get('name', '')).strip()
        specialty = str(body.get('specialty', '')).strip()
        if not 2 <= len(name) <= 100 or not 2 <= len(specialty) <= 100:
            raise Problem('Indica tu nombre y especialidad (entre 2 y 100 caracteres).')
        with self.connection(True) as c:
            previous = c.execute('SELECT name FROM users WHERE id=?', (uid,)).fetchone()
            c.execute('UPDATE users SET name=?,specialty=? WHERE id=?', (name, specialty, uid))
            if previous and previous['name'] != name:
                c.execute('UPDATE legacy_imports SET identity_valid=0 WHERE user_id=?', (uid,))
                c.execute("UPDATE personal_approvals SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at=''",(stamp(now()),uid,uid))
                c.execute("""UPDATE documents SET status='pending',reviewer_id=NULL,
                             note='El nombre del perfil cambió; revisa nuevamente el documento.'
                             WHERE id IN (SELECT MAX(id) FROM documents WHERE user_id=? AND kind IN ('identity','degree') GROUP BY kind)""", (uid,))
                c.execute('UPDATE users SET verified=? WHERE id=?', (int(self.can_book_parallel(c,uid)),uid))
                c.execute("UPDATE notification_outbox SET status='superseded' WHERE kind='document_approved' AND user_id=? AND status IN ('pending','retry','processing')",(uid,))
            self.log(c, uid, 'profile_updated', uid)

    def parse_slot(self, value, uid=None, connection=None):
        owner=False
        if uid is not None:
            if connection is not None:owner=self.can_book_parallel(connection,uid)
            else:
                with self.connection() as c:owner=self.can_book_parallel(c,uid)
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
            local = parsed.astimezone(CHILE)
            if local.minute or local.second or local.microsecond or not OPENING_HOUR <= local.hour < CLOSING_HOUR:
                raise ValueError()
            if parsed.astimezone(timezone.utc) < now().astimezone(timezone.utc) + (timedelta(0) if owner else MIN_BOOKING_NOTICE) or parsed > now() + timedelta(days=90):
                raise ValueError()
            return stamp(parsed)
        except (ValueError, TypeError):
            if owner:raise Problem('Elige un horario futuro con inicio entre 07:00 y 22:00, dentro de los próximos 90 días.')
            raise Problem('Reserva con al menos 24 horas de anticipación, con inicio entre 07:00 y 22:00 (cierre a las 23:00), dentro de los próximos 90 días.')

    def availability(self, date, include_names=False, uid=None):
        try:
            day = datetime.strptime(date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            raise Problem('Fecha inválida.')
        today = now().astimezone(CHILE).date()
        if day < today or day > today + timedelta(days=90):
            raise Problem('Consulta fechas de hoy a los próximos 90 días.')
        with self.connection() as c:
            owner=self.can_book_parallel(c,uid) if uid is not None else False
            occupied = {(r['box'], r['start']): dict(r) for r in c.execute("SELECT b.id,b.user_id,b.box,b.start,u.name,u.email FROM bookings b JOIN users u ON u.id=b.user_id WHERE b.status='confirmed'")}
        result = []
        for hour in range(OPENING_HOUR, CLOSING_HOUR):
            start = datetime(day.year, day.month, day.day, hour, tzinfo=CHILE)
            boxes = []
            for box in range(1,4):
                booking = occupied.get((box, stamp(start)))
                cell = {'box': box, 'available': not booking and start.astimezone(timezone.utc) >= now().astimezone(timezone.utc) + (timedelta(0) if owner else MIN_BOOKING_NOTICE)}
                if include_names and booking:
                    end = start + timedelta(minutes=SESSION_MINUTES)
                    booking['end'] = stamp(end)
                    booking['timing'] = 'in_session' if start <= now() < end else 'buffer' if end <= now() < start + timedelta(hours=1) else 'upcoming' if start > now() else 'finished'
                    cell['booking'] = booking
                boxes.append(cell)
            result.append({'label': f'{hour:02d}:00', 'start': stamp(start), 'end': stamp(start + timedelta(minutes=SESSION_MINUTES)), 'boxes': boxes})
        return {'date': date, 'slots': result}

    def professional(self, c, uid):
        row = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not row or not supports_professional(row) or not row['active'] or row['deleted_at'] or self.email_blocked(c,row['email']) or not self.professional_verified(c, uid):
            raise Problem('Para reservar necesitas una cuenta activa, carnet y certificado de título aprobados por administración, y saldo confirmado.', 403)
        if not row['email_verified']:
            raise Problem('Confirma tu correo electrónico antes de reservar.', 403)
        if row['role']=='professional' and not 2 <= len(row['specialty'].strip()) <= 100:
            raise Problem('Completa tu profesión o especialidad en Mi perfil antes de reservar.', 403)

    def discount_quote(self, c, catalog, code=''):
        if str(catalog) not in CATALOG:raise Problem('Plan inválido.')
        if not isinstance(code,str):raise Problem('Código inválido.')
        code=code.strip().upper()
        price=CATALOG[str(catalog)][1];percent=0
        if code:
            row=c.execute('SELECT * FROM discount_codes WHERE code=? AND active=1',(code,)).fetchone()
            if not row:raise Problem('El código de descuento no existe o está desactivado.')
            percent=row['percent']
        amount=max(1,(price*(100-percent)+50)//100)
        return {'code':code,'percent':percent,'original_price':price,'price':amount,'discount':price-amount}

    def save_discount(self, actor, body):
        code=body.get('code','');percent=body.get('percent');active=body.get('active',True)
        if not isinstance(code,str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,40}',code.strip()):raise Problem('Usa un código de 3 a 40 letras, números, guiones o guiones bajos.')
        code=code.strip().upper()
        if type(percent) is not int or not 1<=percent<=99 or type(active) is not bool:raise Problem('Indica un porcentaje entero entre 1 y 99.')
        with self.connection(True) as c:
            self.require_owner(c,actor)
            c.execute('INSERT INTO discount_codes(code,percent,active,actor_id,created_at) VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET percent=excluded.percent,active=excluded.active',(code,percent,int(active),actor,stamp(now())))
            self.log(c,actor,'discount_updated',code)
        return {'code':code}

    def record_discount(self,c,pid,quote):
        c.execute('UPDATE packages SET discount_code=?,discount_percent=?,original_price=? WHERE id=?',(quote['code'],quote['percent'],quote['original_price'],pid))

    def buy(self, uid, body):
        if not validate_acceptance(body.get('rules_acceptance')):
            raise Problem('Debes leer y aceptar las normas vigentes antes de comprar.',400)
        key = str(body.get('catalog_id', ''))
        if key not in CATALOG:
            raise Problem('Paquete inválido.')
        hours, price = CATALOG[key]
        with self.connection(True) as c:
            row = c.execute('SELECT role,email,active FROM users WHERE id=?', (uid,)).fetchone()
            if not row or not supports_professional(row) or not row['active'] or self.email_blocked(c,row['email']):
                raise Problem('Solo los profesionales activos pueden solicitar paquetes.', 403)
            quote=self.discount_quote(c,key,body.get('discount_code',''));price=quote['price']
            if body.get('expected_price',price)!=price:raise Problem('El precio cambió. Aplica nuevamente el código antes de comprar.',409)
            cur = c.execute('INSERT INTO packages(user_id,hours,remaining,price,created_at) VALUES(?,?,?,?,?)',
                            (uid, hours, hours, price, stamp(now())))
            self.record_discount(c,cur.lastrowid,quote)
            self.record_purchase_acceptance(c,uid,cur.lastrowid,body['rules_acceptance'])
            self.log(c, uid, 'payment_requested_simulated', cur.lastrowid)
            return cur.lastrowid

    def manual_credit(self, actor, body):
        uid, hours, amount=body.get('user_id'),body.get('hours'),body.get('amount')
        method, reference, note, key=body.get('method'),body.get('reference',''),optional_comment(body),body.get('request_key')
        if type(uid) is not int or type(hours) is not int or not 1<=hours<=200 or type(amount) is not int or not 0<=amount<=100000000:
            raise Problem('Selecciona el profesional, entre 1 y 200 sesiones y un monto entero en pesos.')
        if method not in ('cash','transfer','courtesy') or (method=='courtesy' and amount!=0) or (method in ('cash','transfer') and amount<=0):
            raise Problem('Indica el medio y monto recibido. Las cortesías deben tener monto cero.')
        if not isinstance(reference,str) or len(reference.strip())>120 or (len(reference.strip())<3 and not (method=='courtesy' and not reference.strip())):
            raise Problem('Incluye una referencia única de 3 a 120 caracteres. En las cortesías puedes dejarla vacía.')
        if body.get('confirmed') is not True or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',key):
            raise Problem('Confirma que verificaste el pago o autorizaste la cortesía.')
        received_on=body.get('received_on',now().astimezone(CHILE).date().isoformat())
        try:
            if not isinstance(received_on,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',received_on) or datetime.fromisoformat(received_on).date()>now().astimezone(CHILE).date():
                raise ValueError()
        except (ValueError,TypeError):
            raise Problem('Indica una fecha de recepción válida, hasta hoy.')
        reference=reference.strip() or 'CORTESIA-'+hashlib.sha256(key.encode()).hexdigest()[:20]
        fingerprint=hashlib.sha256(json.dumps([uid,hours,amount,method,reference,note,received_on]).encode()).hexdigest()
        try:
            with self.connection(True) as c:
                self.require_management(c,actor)
                previous=c.execute('SELECT package_id,fingerprint FROM manual_credits WHERE actor_id=? AND request_key=?',(actor,key)).fetchone()
                if previous:
                    if previous['fingerprint']!=fingerprint:
                        raise Problem('Esta confirmación corresponde a otra carga. Revisa los datos.',409)
                    return {'package_id':previous['package_id'],'repeated':True}
                recipient=c.execute('SELECT role,email,active FROM users WHERE id=?',(uid,)).fetchone()
                if not recipient or not recipient['active'] or not supports_professional(recipient):
                    raise Problem('Selecciona una cuenta profesional activa.',400)
                if c.execute('SELECT 1 FROM manual_credits WHERE method=? AND reference=? COLLATE NOCASE',(method,reference)).fetchone():
                    raise Problem('Esta referencia ya tiene una carga registrada. No se volvieron a acreditar horas.',409)
                pid=c.execute("INSERT INTO packages(user_id,hours,remaining,price,status,created_at,source) VALUES(?,?,?,?,'paid',?,'manual_credit')",(uid,hours,hours,amount,stamp(now()))).lastrowid
                cid=c.execute('INSERT INTO manual_credits(package_id,user_id,actor_id,method,amount,reference,note,request_key,fingerprint,created_at,received_on) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pid,uid,actor,method,amount,reference,note,key,fingerprint,stamp(now()),received_on)).lastrowid
                self.log(c,actor,'manual_hours_credited',cid)
                self.queue_hours_receipt(c,uid,pid,method)
                return {'package_id':pid,'repeated':False}
        except sqlite3.IntegrityError:
            raise Problem('La carga ya está registrada o sus datos entraron en conflicto. Revisa el historial antes de volver a cargar.',409)

    def require_owner(self,c,actor):
        user=c.execute('SELECT role,email,active FROM users WHERE id=?',(actor,)).fetchone()
        if not user or user['role']!='admin' or user['email']!=ADMIN_EMAIL or not user['active']:
            raise Problem('Acceso exclusivo de administración.',403)

    def adjust_sessions(self,actor,body):
        uid,pid,delta,expected=body.get('user_id'),body.get('package_id'),body.get('delta'),body.get('expected_remaining')
        reason,key=optional_comment(body,'reason'),body.get('request_key')
        if any(type(value) is not int for value in (uid,pid,delta,expected)) or not -200<=delta<=200 or delta==0 or expected<0:
            raise Problem('Indica un plan confirmado y un ajuste de 1 a 200 sesiones, para sumar o descontar.')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',key):
            raise Problem('Vuelve a confirmar el ajuste desde la ficha.')
        reason=reason.strip();fingerprint=hashlib.sha256(json.dumps([uid,pid,delta,expected,reason]).encode()).hexdigest()
        with self.connection(True) as c:
            self.require_owner(c,actor)
            previous=c.execute('SELECT * FROM session_adjustments WHERE actor_id=? AND request_key=?',(actor,key)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise Problem('Esta confirmación corresponde a otro ajuste.',409)
                return {'id':previous['id'],'remaining':previous['after_balance'],'repeated':True}
            plan=c.execute('SELECT * FROM packages WHERE id=? AND user_id=?',(pid,uid)).fetchone()
            if not plan or plan['status']!='paid' or plan['archived_at']:
                raise Problem('Solo puedes ajustar el saldo de un plan confirmado de esta cuenta.',409)
            if plan['remaining']!=expected:raise Problem('El saldo cambió desde que abriste la ficha. Vuelve a consultar antes de ajustar.',409)
            balance=plan['remaining']+delta
            if not 0<=balance<=10000:raise Problem('El ajuste no puede dejar saldo negativo ni superar 10.000 sesiones.',409)
            c.execute('UPDATE packages SET remaining=? WHERE id=?',(balance,pid))
            aid=c.execute('INSERT INTO session_adjustments(user_id,package_id,actor_id,delta,before_balance,after_balance,reason,request_key,fingerprint,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(uid,pid,actor,delta,plan['remaining'],balance,reason,key,fingerprint,stamp(now()))).lastrowid
            self.log(c,actor,'sessions_adjusted',aid)
            return {'id':aid,'remaining':balance,'repeated':False}

    def archive_package(self,actor,body):
        pid,reason=body.get('package_id'),optional_comment(body,'reason')
        if type(pid) is not int:
            raise Problem('Selecciona el plan que deseas eliminar de la vista.')
        with self.connection(True) as c:
            self.require_owner(c,actor)
            plan=c.execute('SELECT * FROM packages WHERE id=?',(pid,)).fetchone()
            if not plan:raise Problem('Plan no encontrado.',404)
            if plan['archived_at']:return {'ok':True,'repeated':True}
            if plan['status'] not in ('pending','rejected') or c.execute('SELECT 1 FROM bookings WHERE package_id=?',(pid,)).fetchone():
                raise Problem('Solo se pueden archivar planes sin pago acreditado y sin reservas asociadas.',409)
            payment=c.execute('SELECT * FROM payment_attempts WHERE package_id=?',(pid,)).fetchone()
            if payment and (payment['state'] not in ('ready','rejected') or payment['processing_until']>time.time() or (payment['state']=='ready' and payment['commit_requested'])):
                raise Problem('Este pago requiere comprobación antes de archivarlo. Consulta su estado y vuelve a intentarlo.',409)
            c.execute('INSERT INTO package_archive_events(package_id,actor_id,reason,created_at) VALUES(?,?,?,?)',(pid,actor,reason.strip(),stamp(now())))
            c.execute('UPDATE packages SET archived_at=?,archive_reason=?,archived_by=? WHERE id=?',(stamp(now()),reason.strip(),actor,pid))
            if payment:c.execute('UPDATE payment_attempts SET archived=1 WHERE id=?',(payment['id'],))
            self.log(c,actor,'package_archived',pid)
            return {'ok':True,'repeated':False}

    def accept_manual_rules(self, uid, body):
        pid=body.get('package_id')
        if type(pid) is not int or not validate_acceptance(body.get('rules_acceptance')):
            raise Problem('Debes leer y aceptar las normas vigentes de este plan.')
        with self.connection(True) as c:
            plan=c.execute("SELECT id FROM packages WHERE id=? AND user_id=? AND source='manual_credit' AND status='paid'",(pid,uid)).fetchone()
            if not plan:
                raise Problem('Plan no encontrado.',404)
            if not c.execute('SELECT 1 FROM purchase_acceptances WHERE package_id=?',(pid,)).fetchone():
                self.record_purchase_acceptance(c,uid,pid,body['rules_acceptance'])
                self.log(c,uid,'manual_plan_rules_accepted',pid)
        return {'ok':True}

    def can_book_parallel(self,c,uid):
        user=c.execute('SELECT role,email,active FROM users WHERE id=?',(uid,)).fetchone()
        return bool(user and user['role']=='admin' and user['email']==ADMIN_EMAIL and user['active'])

    def record_booking_rules(self,c,uid,bid,modification=False):
        c.execute('INSERT INTO booking_rule_acceptances(booking_id,user_id,version,rules_text,accepted_at,is_modification) VALUES(?,?,?,?,?,?)',(bid,uid,RULES_VERSION,RULES_TEXT,stamp(now()),int(modification)))

    def reserve_one(self, c, uid, box, start, receipt_balance=None, rules_acceptance=None):
        self.parse_slot(start,uid,c)
        if not self.can_book_parallel(c,uid) and c.execute("SELECT id FROM bookings WHERE user_id=? AND start=? AND status='confirmed'", (uid, start)).fetchone():
            raise Problem('Ya tienes una reserva a esa hora.', 409)
        package=c.execute("SELECT * FROM packages WHERE user_id=? AND status='paid' AND remaining>0 ORDER BY id LIMIT 1",(uid,)).fetchone()
        if not package:
            raise Problem('Necesitas saldo de un paquete con pago confirmado.',409)
        if package['source']=='manual_credit' and not c.execute('SELECT 1 FROM purchase_acceptances WHERE package_id=?',(package['id'],)).fetchone():
            if not validate_acceptance(rules_acceptance):raise Problem('Debes aceptar las normas antes de confirmar la reserva.',403)
            self.record_purchase_acceptance(c,uid,package['id'],rules_acceptance)
        bid=c.execute('INSERT INTO bookings(user_id,package_id,box,start,created_at) VALUES(?,?,?,?,?)',(uid,package['id'],box,start,stamp(now()))).lastrowid
        if validate_acceptance(rules_acceptance):self.record_booking_rules(c,uid,bid)
        c.execute('UPDATE packages SET remaining=remaining-1 WHERE id=?',(package['id'],))
        self.log(c,uid,'reserved',bid)
        self.queue_booking_receipt(c,uid,bid,'booking_confirmed',remaining=receipt_balance)
        return bid

    def reserve(self, uid, body):
        if 'rules_acceptance' in body and not validate_acceptance(body['rules_acceptance']):raise Problem('Debes aceptar las normas vigentes antes de reservar.',400)
        start=self.parse_slot(body.get('start'),uid); box=body.get('box')
        if type(box) is not int or box not in (1,2,3):
            raise Problem('Box inválido.')
        try:
            with self.connection(True) as c:
                self.professional(c,uid)
                return self.reserve_one(c,uid,box,start,rules_acceptance=body.get('rules_acceptance'))
        except sqlite3.IntegrityError:
            raise Problem('Ese box acaba de ser reservado. Elige otro horario.',409)

    def reserve_batch(self, uid, body):
        if 'rules_acceptance' in body and not validate_acceptance(body['rules_acceptance']):raise Problem('Debes aceptar las normas vigentes antes de reservar.',400)
        selected=body.get('slots'); key=body.get('request_key')
        if not isinstance(selected,list) or not 1<=len(selected)<=200 or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',key):
            raise Problem('Selecciona entre 1 y 200 horas y vuelve a confirmar la selección.')
        slots=[]
        for item in selected:
            if not isinstance(item,dict) or type(item.get('box')) is not int or item['box'] not in (1,2,3):
                raise Problem('Box inválido en la selección.')
            slots.append((self.parse_slot(item.get('start'),uid),item['box']))
        if len(set(slots))!=len(slots):
            raise Problem('No puedes seleccionar dos veces el mismo box y horario.',409)
        slots.sort()
        fingerprint=hashlib.sha256(json.dumps(slots).encode()).hexdigest()
        try:
            with self.connection(True) as c:
                self.professional(c,uid)
                if not self.can_book_parallel(c,uid) and len({start for start,box in slots})!=len(slots):
                    raise Problem('No puedes seleccionar dos boxes para la misma hora.',409)
                previous=c.execute('SELECT * FROM booking_batches WHERE user_id=? AND request_key=?',(uid,key)).fetchone()
                if previous:
                    if previous['fingerprint']!=fingerprint:
                        raise Problem('Esta confirmación corresponde a otra selección. Revisa la agenda.',409)
                    remaining=c.execute("SELECT COALESCE(SUM(remaining),0) FROM packages WHERE user_id=? AND status='paid'",(uid,)).fetchone()[0]
                    return {'ids':json.loads(previous['booking_ids']),'repeated':True,'remaining':remaining}
                balance=c.execute("SELECT COALESCE(SUM(remaining),0) FROM packages WHERE user_id=? AND status='paid'",(uid,)).fetchone()[0]
                if balance<len(slots):
                    raise Problem('Tu saldo no alcanza para todas las horas seleccionadas. No se confirmó ninguna.',409)
                ids=[self.reserve_one(c,uid,box,start,receipt_balance=balance-len(slots),rules_acceptance=body.get('rules_acceptance')) for start,box in slots]
                c.execute('INSERT INTO booking_batches VALUES(?,?,?,?,?)',(uid,key,fingerprint,json.dumps(ids),stamp(now())))
                return {'ids':ids,'repeated':False,'remaining':balance-len(slots)}
        except sqlite3.IntegrityError:
            raise Problem('Un horario seleccionado acaba de ocuparse. No se confirmó ninguna hora ni se descontó saldo; revisa la agenda.',409)

    def modify(self, uid, bid, body):
        if 'rules_acceptance' in body and not validate_acceptance(body['rules_acceptance']):raise Problem('Debes aceptar las normas vigentes antes de reservar.',400)
        start = self.parse_slot(body.get('start'),uid)
        box = body.get('box')
        if type(box) is not int or box not in (1, 2, 3):
            raise Problem('Box inválido.')
        try:
            with self.connection(True) as c:
                self.professional(c, uid)
                self.parse_slot(start,uid,c)
                booking = c.execute('SELECT * FROM bookings WHERE id=? AND user_id=?', (bid, uid)).fetchone()
                if not booking:
                    raise Problem('Reserva no encontrada.', 404)
                owner=self.can_book_parallel(c,uid)
                if booking['status'] != 'confirmed' or (booking['modifications'] >= 1 and not owner):
                    raise Problem('Esta reserva ya no admite modificaciones.', 409)
                cutoff = now() + (timedelta(0) if owner else timedelta(hours=2))
                if datetime.fromisoformat(booking['start']) < cutoff or datetime.fromisoformat(start) < cutoff:
                    raise Problem('Solo puedes modificar reservas que todavía no hayan comenzado.' if owner else 'El cambio requiere al menos dos horas de anticipación para ambos horarios.', 409)
                if booking['box'] == box and booking['start'] == start:
                    raise Problem('Elige un box u horario diferente; tu cambio sigue disponible.')
                if not self.can_book_parallel(c,uid) and c.execute("SELECT id FROM bookings WHERE user_id=? AND start=? AND status='confirmed' AND id<>?", (uid, start, bid)).fetchone():
                    raise Problem('Ya tienes una reserva a esa hora.', 409)
                c.execute('UPDATE bookings SET box=?,start=?,modifications=modifications+1 WHERE id=?', (box, start, bid))
                if validate_acceptance(body.get('rules_acceptance')):self.record_booking_rules(c,uid,bid,True)
                self.log(c, uid, 'modified', bid)
                self.queue_booking_receipt(c,uid,bid,'booking_modified')
        except sqlite3.IntegrityError:
            raise Problem('Ese horario ya está ocupado. Tu reserva original sigue vigente.', 409)

    def upload(self, uid, body):
        kind = body.get('kind', 'accreditation')
        if kind not in ('identity', 'degree', 'accreditation'):
            raise Problem('Tipo de documento inválido.')
        files = body.get('files', [body])
        if not isinstance(files, list) or not 1 <= len(files) <= 5:
            raise Problem('Selecciona entre 1 y 5 archivos.')
        prepared = []
        total = 0
        for file in files:
            if not isinstance(file, dict):
                raise Problem('Archivo inválido.')
            filename = Path(str(file.get('filename', '')).replace('\\', '/')).name
            filename = ''.join(char for char in filename if ord(char) >= 32).strip()[:180]
            if not filename:
                raise Problem('Cada archivo necesita un nombre.')
            try:
                raw = base64.b64decode(file.get('content', ''), validate=True)
            except (ValueError, TypeError, binascii.Error):
                raise Problem('Archivo inválido.')
            total += len(raw)
            if not raw or total > MAX_UPLOAD_BYTES:
                raise Problem('Los archivos deben tener contenido y sumar como máximo 10 MB.')
            prepared.append((filename, raw))
        # All file types remain private binary attachments; none are rendered or executed.
        with self.connection(True) as c:
            role = c.execute('SELECT role,email,active,deleted_at FROM users WHERE id=?', (uid,)).fetchone()
            if not supports_professional(role) or not role['active'] or role['deleted_at'] or self.email_blocked(c,role['email']):
                raise Problem('Solo profesionales pueden adjuntar documentos.', 403)
            c.execute("UPDATE notification_outbox SET status='superseded' WHERE kind='document_approved' AND user_id=? AND entity_id IN (SELECT id FROM documents WHERE user_id=? AND kind=?) AND status IN ('pending','retry','processing')",(uid,uid,kind))
            filename, raw = prepared[0]
            cursor = c.execute("INSERT INTO documents(user_id,filename,mime,content,created_at,kind) VALUES(?,?,'application/octet-stream',?,?,?)", (uid, filename, raw, stamp(now()), kind))
            for position, (filename, raw) in enumerate(prepared[1:], 2):
                c.execute("INSERT INTO document_attachments VALUES(?,?,?,'application/octet-stream',?)", (cursor.lastrowid, position, filename, raw))
            if kind == 'identity':
                c.execute("UPDATE personal_approvals SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at=''",(stamp(now()),uid,uid))
                c.execute('UPDATE legacy_imports SET identity_valid=0 WHERE user_id=?', (uid,))
                c.execute('UPDATE users SET verified=0 WHERE id=?', (uid,))
            c.execute('UPDATE users SET verified=? WHERE id=?',(int(self.professional_verified(c,uid)),uid))
            self.log(c, uid, 'document_uploaded', cursor.lastrowid)
            person=c.execute('SELECT name,email FROM users WHERE id=?',(uid,)).fetchone()
            self.queue_notification(c,uid,'admin_document',cursor.lastrowid,ADMIN_EMAIL,dict(person)|{'document_id':cursor.lastrowid,'document_kind':kind,'file_count':len(prepared)})

    def admin_state(self,actor=None):
        with self.connection() as c:
            if actor is not None:self.require_management(c,actor)
            data = {'blocked_emails':[dict(r) for r in c.execute('SELECT b.*,u.name AS operator_name FROM blocked_emails b JOIN users u ON u.id=b.actor_id WHERE b.active=1 ORDER BY b.created_at DESC')], 'user_access_events':[dict(r) for r in c.execute('SELECT e.*,u.name AS operator_name FROM user_access_events e JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC')], 'personal_approvals':[dict(r) for r in c.execute('SELECT p.*,u.name AS operator_name FROM personal_approvals p JOIN users u ON u.id=p.actor_id ORDER BY p.id DESC')], 'discount_codes':[dict(row) for row in c.execute('SELECT * FROM discount_codes ORDER BY created_at DESC,code')], 'package_archive_events':[dict(row) for row in c.execute('SELECT e.*,p.user_id,u.name AS operator_name FROM package_archive_events e JOIN packages p ON p.id=e.package_id JOIN users u ON u.id=e.actor_id ORDER BY e.id DESC')], 'session_adjustments':[dict(row) for row in c.execute('SELECT j.*,u.name,actor.name AS operator_name FROM session_adjustments j JOIN users u ON u.id=j.user_id JOIN users actor ON actor.id=j.actor_id ORDER BY j.id DESC')], 'manual_credits':[dict(row) for row in c.execute('SELECT m.id,m.package_id,m.user_id,m.method,m.amount,m.reference,m.note,m.created_at,m.received_on,p.hours,p.remaining,u.name,u.email,actor.name AS operator_name FROM manual_credits m JOIN packages p ON p.id=m.package_id JOIN users u ON u.id=m.user_id JOIN users actor ON actor.id=m.actor_id ORDER BY m.id DESC')], 'notification_queue': [dict(r) for r in c.execute('SELECT n.id,n.kind,n.status,n.attempts,n.created_at,u.name FROM notification_outbox n JOIN users u ON u.id=n.user_id ORDER BY n.id DESC LIMIT 30')],
                    'mail_events': [dict(r) for r in c.execute('SELECT e.id,e.kind,e.provider,e.status,e.category,e.http_status,e.created_at,u.name FROM mail_events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 30')],
                    'payments': [dict(r) for r in c.execute('SELECT p.id,p.package_id,p.user_id,p.buy_order,p.amount,p.hours,p.environment,p.state,p.created_at,p.approved_at,p.archived,u.name FROM payment_attempts p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC')],
                    'migrations': [dict(r) for r in c.execute('SELECT l.*,u.name AS operator_name FROM legacy_imports l JOIN users u ON u.id=l.actor_id ORDER BY l.id DESC')],
                    'users': [dict(r) | {'verified':int(self.professional_verified(c,r['id'])),'personally_approved':self.personally_approved(c,r['id']),'owner_booking_privileges':self.can_book_parallel(c,r['id']),'management_granted':bool(c.execute('SELECT 1 FROM management_grants WHERE email=? AND active=1',(r['email'],)).fetchone()),'blocked_email':self.email_blocked(c,r['email'])} for r in c.execute('SELECT id,name,email,specialty,role,verified,active,email_verified,deleted_at FROM users ORDER BY id')],
                    'documents': [self.document_metadata(c,r) for r in c.execute('SELECT d.id,d.user_id,d.filename,d.status,d.note,d.created_at,d.kind,u.name FROM documents d JOIN users u ON u.id=d.user_id ORDER BY d.id DESC')],
                    'packages': [dict(r) for r in c.execute('SELECT p.*,COALESCE((SELECT SUM(delta) FROM session_adjustments j WHERE j.package_id=p.id),0) AS balance_adjustment,u.name,a.version AS rules_version,a.accepted_at AS rules_accepted_at,m.method AS manual_method FROM packages p JOIN users u ON u.id=p.user_id LEFT JOIN purchase_acceptances a ON a.package_id=p.id LEFT JOIN manual_credits m ON m.package_id=p.id ORDER BY p.id DESC')],
                    'bookings': [booking_details(r) for r in c.execute('SELECT b.*,u.name FROM bookings b JOIN users u ON u.id=b.user_id ORDER BY b.start')],
                    'audit': [dict(r) for r in c.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 30')]}

            if actor is not None and not self.can_book_parallel(c,actor):
                allowed={'users','packages','bookings','payments','manual_credits','session_adjustments','package_archive_events'}
                data={key:(value if key in allowed else []) for key,value in data.items()}
            return data

    def admin_action(self, actor, body):
        action, target = body.get('action'), body.get('id')
        if type(target) is not int:
            raise Problem('Identificador inválido.')
        with self.connection(True) as c:
            if action == 'personal_approval':
                self.require_owner(c,actor)
                row=c.execute('SELECT * FROM users WHERE id=?',(target,)).fetchone()
                if not row or row['role']!='professional' or not row['active']:raise Problem('Selecciona una cuenta profesional activa.',403)
                decision=body.get('decision')
                if decision not in ('approved','revoked') or body.get('confirmed') is not True:raise Problem('Confirma expresamente la aprobación personal o su retiro.')
                note=optional_comment(body)
                if decision=='approved':
                    if self.personally_approved(c,target):return
                    c.execute("UPDATE personal_approvals SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at=''",(stamp(now()),actor,target))
                    c.execute('INSERT INTO personal_approvals(user_id,actor_id,approved_name,note,created_at) VALUES(?,?,?,?,?)',(target,actor,row['name'],note,stamp(now())))
                else:
                    c.execute("UPDATE personal_approvals SET revoked_at=?,revoked_by=? WHERE user_id=? AND revoked_at=''",(stamp(now()),actor,target))
                c.execute('UPDATE users SET verified=? WHERE id=?',(int(self.professional_verified(c,target)),target))
            elif action == 'payment':
                row = c.execute('SELECT * FROM packages WHERE id=?', (target,)).fetchone()
                if not row:
                    raise Problem('Paquete no encontrado.', 404)
                if row['source'] != 'simulated':
                    raise Problem('Este paquete no es un pago simulado y no admite confirmación manual.', 403)
                if row['archived_at']:raise Problem('Este plan fue archivado y no admite confirmación manual.',409)
                if row['status'] != 'pending':
                    raise Problem('El pago ya fue procesado.', 409)
                decision = body.get('decision')
                if decision not in ('paid', 'rejected'):
                    raise Problem('Decisión inválida.')
                c.execute('UPDATE packages SET status=? WHERE id=?', (decision, target))
            elif action == 'document':
                row = c.execute('SELECT * FROM documents WHERE id=?', (target,)).fetchone()
                if not row:
                    raise Problem('Documento no encontrado.', 404)
                latest = c.execute('SELECT MAX(id) AS id FROM documents WHERE user_id=? AND kind=?', (row['user_id'], row['kind'])).fetchone()['id']
                if target != latest:
                    raise Problem('Revisa el documento más reciente de este profesional.', 409)
                decision = body.get('decision')
                note = optional_comment(body)
                if decision not in ('approved', 'rejected'):
                    raise Problem('Indica una decisión válida para el documento.')
                if row['kind'] == 'identity' and decision == 'approved' and body.get('identity_confirmed') is not True:
                    raise Problem('Confirma que revisaste ambos lados del carnet y que corresponden al titular.')
                c.execute('UPDATE documents SET status=?,note=?,reviewer_id=? WHERE id=?', (decision, note, actor, target))
                c.execute('UPDATE users SET verified=? WHERE id=?', (int(self.professional_verified(c, row['user_id'])), row['user_id']))
                if decision=='approved' and row['status']!='approved' and row['kind'] in ('identity','degree'):
                    person=c.execute('SELECT name,email FROM users WHERE id=?',(row['user_id'],)).fetchone()
                    self.queue_notification(c,row['user_id'],'document_approved',target,person['email'],dict(person)|{'document_kind':row['kind']},event_key=f'document_approved:{target}:'+secrets.token_hex(12))
                elif decision=='rejected':
                    c.execute("UPDATE notification_outbox SET status='superseded' WHERE kind='document_approved' AND entity_id=? AND status IN ('pending','retry','processing')",(target,))
            elif action == 'user':
                row = c.execute('SELECT * FROM users WHERE id=?', (target,)).fetchone()
                if not row or row['role'] == 'admin' or row['deleted_at']:
                    raise Problem('Solo puedes gestionar cuentas profesionales.', 403)
                if type(body.get('active')) is not bool:
                    raise Problem('Estado inválido.')
                c.execute('UPDATE users SET active=? WHERE id=?', (int(body['active']), target))
                if not body['active']:
                    c.execute('DELETE FROM sessions WHERE user_id=?', (target,))
            elif action == 'cancel':
                row = c.execute('SELECT * FROM bookings WHERE id=?', (target,)).fetchone()
                if not row or row['status'] != 'confirmed':
                    raise Problem('Reserva no cancelable.', 409)
                if datetime.fromisoformat(row['start']) <= now():
                    raise Problem('No se pueden cancelar reservas pasadas.', 409)
                c.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (target,))
                c.execute('UPDATE packages SET remaining=remaining+1 WHERE id=?', (row['package_id'],))
                self.queue_booking_receipt(c,row['user_id'],target,'booking_cancelled')
            else:
                raise Problem('Acción inválida.')
            self.log(c, actor, 'admin_' + action + '_' + str(body.get('decision', '')), target)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ValhallaPrototype'
    recovery_lock = threading.Lock()
    recovery_clients = {}

    def log_request(self, code='-', size='-'):
        from urllib.parse import urlsplit
        self.log_message('%s %s %s', self.command, urlsplit(self.path).path, code)

    def email_diagnostics(self):
        delivery = getattr(self.server, 'email_delivery', None)
        if delivery and hasattr(delivery,'diagnostics'):
            return delivery.diagnostics()
        return {'configured':bool(delivery and delivery.configured), 'provider':'test' if delivery else 'none',
                'api_key_present':False, 'sender':'', 'public_url':''}

    def deliver_email(self, kind, uid, email, token=None):
        delivery = getattr(self.server, 'email_delivery', None)
        provider = self.email_diagnostics()['provider']
        eid = self.server.store.mail_event(uid,kind,provider,'pending')
        try:
            if kind=='test':
                delivery.send_test(email)
            elif kind=='verification':
                delivery.send_verification(email,token)
            else:
                delivery.send_password_reset(email,token)
        except RuntimeError as error:
            category = error.category if isinstance(error,EmailDeliveryError) else 'delivery_error'
            code = error.http_status if isinstance(error,EmailDeliveryError) else None
            self.server.store.mail_outcome(eid,'error',category,code)
            # Safe operational log: fixed category and status only, no error body or addresses.
            self.log_message('Email delivery failed: category=%s http=%s',category,code or '-')
            return False
        self.server.store.mail_outcome(eid,'accepted')
        return True

    def send_email_verification(self, uid):
        delivery = getattr(self.server, 'email_delivery', None)
        if not delivery or not delivery.configured:
            return {'sent': False, 'available': False, 'message': 'Tu cuenta está creada, pero el envío de correos todavía no está habilitado. No podrás reservar hasta confirmar tu correo.'}
        token, email = self.server.store.issue_email_token(uid)
        if token is None:
            return {'sent': False, 'available': True, 'verified': True}
        if self.deliver_email('verification',uid,email,token):
            return {'sent': True, 'available': True, 'message': 'Te enviamos un enlace de confirmación. Revisa tu correo y la carpeta de spam.'}
        return {'sent': False, 'available': True, 'message': 'No se pudo enviar la confirmación. Tu cuenta está creada; administración puede revisar el diagnóstico de correo. Solicita otro enlace en un minuto.'}

    def headers_common(self, cache='no-store'):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cache-Control', cache)
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self' https://webpay3gint.transbank.cl https://webpay3g.transbank.cl; frame-ancestors 'none'")

    def respond(self, data, status=200, cookie=None):
        raw = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.headers_common()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(raw)

    def cookie(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            return cookies['session'].value if 'session' in cookies else ''
        except Exception:
            return ''

    def body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BODY:
                raise Problem('Solicitud demasiado grande o vacía.', 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, json.JSONDecodeError):
            raise Problem('Solicitud JSON inválida.')

    def admin(self, user, management=False):
        with self.server.store.connection() as c:
            if management:self.server.store.require_management(c,user['id'])
            else:self.server.store.require_owner(c,user['id'])

    def webpay_return(self, fields):
        service = getattr(self.server, 'webpay', None)
        try:
            if not service:
                raise PaymentError('Webpay no está habilitado.')
            result = service.returned(fields)
            status = result['state']
        except (PaymentError, sqlite3.Error):
            status = 'pending'
        self.send_response(303)
        self.headers_common()
        self.send_header('Location', '/?payment_result=' + status)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def start_google(self, link_session=''):
        gateway = getattr(self.server, 'google', None)
        if not gateway or not gateway.configured:
            raise Problem('El acceso con Google todavía no está habilitado.', 503)
        if 'https://' + self.headers.get('Host', '') != gateway.origin:
            raise Problem('Para continuar con Google, abre ' + gateway.origin + '.', 400)
        try:
            state, browser, verifier = google_auth.begin(self.server.store, link_session)
        except google_auth.GoogleError:
            raise Problem('No se pudo vincular esta cuenta con Google.', 403)
        self.respond({'authorization_url':gateway.authorization_url(state, verifier)}, cookie=f'google_flow={browser}; HttpOnly; SameSite=Lax; Secure; Path=/api/google; Max-Age=600')

    def google_callback(self, query):
        from urllib.parse import parse_qs
        token, result = None, 'error'
        try:
            gateway = getattr(self.server, 'google', None)
            if not gateway or not gateway.configured or len(query)>8192:
                raise google_auth.GoogleError()
            fields = parse_qs(query, max_num_fields=10)
            if any(len(v)!=1 for v in fields.values()):
                raise google_auth.GoogleError()
            cookies = SimpleCookie(self.headers.get('Cookie',''))
            browser = cookies['google_flow'].value if 'google_flow' in cookies else ''
            flow = google_auth.consume(self.server.store, fields.get('state',[''])[0], browser)
            if 'error' in fields:
                raise google_auth.GoogleError('cancelled')
            code = fields.get('code',[''])[0]
            if not code or len(code)>4096:
                raise google_auth.GoogleError()
            identity = gateway.exchange(code, flow['verifier'])
            token, result = google_auth.authenticate(self.server.store, identity, flow['link_session'])
        except google_auth.GoogleError as error:
            result = error.result
        except (ValueError, KeyError, CookieError, sqlite3.Error):
            result = 'error'
        self.send_response(303)
        self.headers_common()
        self.send_header('Location', '/?google_result=' + result)
        self.send_header('Set-Cookie', 'google_flow=; HttpOnly; SameSite=Lax; Secure; Path=/api/google; Max-Age=0')
        if token:
            self.send_header('Set-Cookie', f'session={token}; HttpOnly; SameSite=Strict; Secure; Path=/; Max-Age=28800')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def start_microsoft(self, link_session=''):
        gateway = getattr(self.server, 'microsoft', None)
        if not gateway or not gateway.configured:
            raise Problem('El acceso con Microsoft todavía no está habilitado.', 503)
        if 'https://' + self.headers.get('Host', '') != gateway.origin:
            raise Problem('Para continuar con Microsoft, abre ' + gateway.origin + '.', 400)
        try:
            state, browser, verifier = microsoft_auth.begin(self.server.store, link_session)
        except microsoft_auth.MicrosoftError:
            raise Problem('No se pudo vincular esta cuenta con Microsoft.', 403)
        self.respond({'authorization_url':gateway.authorization_url(state, verifier)}, cookie=f'microsoft_flow={browser}; HttpOnly; SameSite=Lax; Secure; Path=/api/microsoft; Max-Age=600')

    def microsoft_callback(self, query):
        from urllib.parse import parse_qs
        token, result = None, 'error'
        try:
            gateway = getattr(self.server, 'microsoft', None)
            if not gateway or not gateway.configured or len(query)>8192:
                raise microsoft_auth.MicrosoftError()
            fields = parse_qs(query, max_num_fields=10)
            if any(len(v)!=1 for v in fields.values()):
                raise microsoft_auth.MicrosoftError()
            cookies = SimpleCookie(self.headers.get('Cookie',''))
            browser = cookies['microsoft_flow'].value if 'microsoft_flow' in cookies else ''
            flow = microsoft_auth.consume(self.server.store, fields.get('state',[''])[0], browser)
            if 'error' in fields:
                raise microsoft_auth.MicrosoftError('cancelled')
            code = fields.get('code',[''])[0]
            if not code or len(code)>4096:
                raise microsoft_auth.MicrosoftError()
            identity = gateway.exchange(code, flow['verifier'])
            token, result = microsoft_auth.authenticate(self.server.store, identity, flow['link_session'])
        except microsoft_auth.MicrosoftError as error:
            result = error.result
        except (ValueError, KeyError, CookieError, sqlite3.Error):
            result = 'error'
        self.send_response(303)
        self.headers_common()
        self.send_header('Location', '/?microsoft_result=' + result)
        self.send_header('Set-Cookie', 'microsoft_flow=; HttpOnly; SameSite=Lax; Secure; Path=/api/microsoft; Max-Age=0')
        if token:
            self.send_header('Set-Cookie', f'session={token}; HttpOnly; SameSite=Strict; Secure; Path=/; Max-Age=28800')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        route = urlparse(self.path)
        try:
            if route.path == '/api/microsoft/config':
                self.respond({'enabled': bool(getattr(getattr(self.server, 'microsoft', None), 'configured', False))})
                return
            if route.path == '/api/microsoft/callback':
                self.microsoft_callback(route.query)
                return
            if route.path == '/api/google/config':
                self.respond({'enabled': bool(getattr(getattr(self.server, 'google', None), 'configured', False))})
                return
            if route.path == '/api/google/callback':
                self.google_callback(route.query)
                return
            if route.path == '/api/webpay/return':
                query = parse_qs(route.query)
                if len(route.query)>8192 or any(len(values)!=1 for values in query.values()):
                    raise Problem('Retorno de pago inválido.')
                self.webpay_return({key:values[0] for key,values in query.items()})
                return
            if route.path == '/normas':
                from html import escape
                content=''.join('<li><h2>'+escape(title)+'</h2><p>'+escape(text)+'</p></li>' for title,text in [(item['title'],item['text']) for item in purchase_rules()['items']])
                raw=('<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Normas de uso · Valhalla Office</title><link rel="icon" type="image/png" href="/images/valhalla-logo.png"><link rel="stylesheet" href="/style.css"></head><body><header class="legal-header"><a href="/">VALHALLA OFFICE</a></header><main class="legal-content"><h1>Normas de uso</h1><p>Debes leer y aceptar estas normas antes de comprar un plan. Versión: '+RULES_VERSION+'</p><ol>'+content+'</ol><p><a href="/condiciones">Condiciones del servicio</a> · <a href="/privacidad">Privacidad</a></p></main></body></html>').encode()
                self.send_response(200); self.headers_common(); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
                return
            if not route.path.startswith('/api/'):
                files = {'/propuestas-iconos': ('propuestas-iconos.html', 'text/html'), '/propuestas-iconos.css': ('propuestas-iconos.css', 'text/css'), '/privacidad': ('privacidad.html', 'text/html'), '/condiciones': ('condiciones.html', 'text/html'), '/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
                if route.path.startswith('/images/'):
                    asset = image_asset(route.path.removeprefix('/images/'))
                    if asset:
                        files[route.path] = (str(asset[0]), asset[1])
                if route.path in ('/images/box-1', '/images/box-2', '/images/box-3'):
                    photo = room_photo(int(route.path[-1]))
                    if photo:
                        files[route.path] = (str(photo[0]), photo[1])
                if route.path not in files:
                    raise Problem('Página no encontrada.', 404)
                filename, mime = files[route.path]
                raw = (ROOT / 'static' / filename).read_bytes()
                self.send_response(200)
                self.headers_common('public, max-age=86400' if mime.startswith('image/') else 'no-store')
                self.send_header('Content-Type', mime if mime.startswith('image/') else mime + '; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if route.path == '/api/gallery':
                self.respond({'groups': gallery_items(), 'catalog': [{'hours': hours, 'price': price} for hours, price in CATALOG.values()]})
                return
            user = self.server.store.session(self.cookie())
            if route.path == '/api/me':
                data = self.server.store.state(user)
                data['webpay_live_sales_enabled']=self.server.webpay.live_configuration()['sales_enabled'] if getattr(self.server,'webpay',None) else False
                data['webpay_mode'] = getattr(getattr(self.server, 'webpay', None), 'mode', 'disabled')
                data['email_delivery_available'] = bool(getattr(self.server, 'email_delivery', None) and self.server.email_delivery.configured)
                self.respond(data)
            elif route.path == '/api/admin/daily-contacts':
                self.admin(user)
                self.respond(self.server.store.daily_contacts(parse_qs(route.query).get('date',[''])[0]))
            elif route.path == '/api/admin/availability':
                self.admin(user,management=True)
                self.respond(self.server.store.availability(parse_qs(route.query).get('date', [''])[0], include_names=True,uid=user['id']))
            elif route.path == '/api/availability':
                self.respond(self.server.store.availability(parse_qs(route.query).get('date', [''])[0],uid=user['id']))
            elif route.path == '/api/admin':
                self.admin(user,management=True)
                data=self.server.store.admin_state(user['id'])
                if user['role']=='admin':
                    data['email_config']=self.email_diagnostics()
                    data['webpay_config']=self.server.webpay.configuration() if getattr(self.server,'webpay',None) else {'mode':'disabled','commerce_code':'','sandbox_enabled':False,'production_available':False}
                self.respond(data)
            elif route.path.startswith('/api/documents/'):
                match = re.fullmatch(r'/api/documents/(\d+)(?:/files/(\d+))?', route.path)
                if not match:
                    raise Problem('Documento no encontrado.', 404)
                did, position = int(match[1]), int(match[2] or 1)
                with self.server.store.connection() as c:
                    document = c.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
                    if not document or (user['role'] != 'admin' and user['id'] != document['user_id']):
                        raise Problem('Documento no encontrado.', 404)
                    doc = document if position == 1 else c.execute('SELECT * FROM document_attachments WHERE document_id=? AND position=?', (did, position)).fetchone()
                    if not doc:
                        raise Problem('Archivo no encontrado.', 404)
                from urllib.parse import quote
                self.send_response(200)
                self.headers_common()
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', "attachment; filename=\"archivo-" + str(did) + "\"; filename*=UTF-8''" + quote(doc['filename'], safe=''))
                self.send_header('Content-Length', str(len(doc['content'])))
                self.end_headers()
                self.wfile.write(doc['content'])
            else:
                raise Problem('Ruta no encontrada.', 404)
        except Problem as e:
            self.respond({'error': e.message}, e.status)
        except sqlite3.Error:
            self.respond({'error': 'No se pudo consultar la información. Inténtalo nuevamente.'}, 503)

    def do_POST(self):
        try:
            if self.path == '/api/webpay/return':
                from urllib.parse import parse_qs
                if self.headers.get('Content-Type','').split(';')[0] != 'application/x-www-form-urlencoded':
                    raise Problem('Retorno de pago inválido.',415)
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0<size<=8192:
                        raise ValueError()
                    query = parse_qs(self.rfile.read(size).decode('utf-8'), max_num_fields=10)
                    if any(len(values)!=1 for values in query.values()):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    raise Problem('Retorno de pago inválido.')
                self.webpay_return({key:values[0] for key,values in query.items()})
                return
            # JSON-only public endpoints and same-origin checks prevent login CSRF.
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise Problem('Se requiere JSON.', 415)
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')):
                raise Problem('Origen no permitido.', 403)
            body = self.body()
            if self.path == '/api/microsoft/start':
                self.start_microsoft()
                return
            if self.path == '/api/google/start':
                self.start_google()
                return
            if self.path == '/api/password-reset/request':
                delivery = getattr(self.server, 'email_delivery', None)
                if not delivery or not delivery.configured:
                    raise Problem('La recuperación por correo todavía no está habilitada. Inténtalo cuando administración configure el servicio.', 503)
                # Bound outbound mail per client as well as per email, without revealing account existence.
                with self.recovery_lock:
                    client = self.client_address[0]
                    timestamp = time.time()
                    Handler.recovery_clients = {key: entries for key, values in Handler.recovery_clients.items() if (entries := [t for t in values if timestamp-t < 3600])}
                    entries = Handler.recovery_clients.setdefault(client, [])
                    allowed = len(entries) < 10
                    if allowed:
                        entries.append(timestamp)
                result = self.server.store.issue_password_reset(body.get('email', '')) if allowed else None
                if result:
                    with self.server.store.connection() as c:
                        recipient=c.execute('SELECT id FROM users WHERE email=?',(result[1],)).fetchone()[0]
                    self.deliver_email('recovery',recipient,result[1],result[0])
                elif allowed:
                    self.server.store.mail_event(None,'recovery',self.email_diagnostics()['provider'],'skipped','account_or_limit')
                self.respond({'ok': True, 'message': 'Si existe una cuenta profesional activa con ese correo, recibirás un enlace. Revisa también spam. Puedes solicitar otro después de un minuto.'})
                return
            if self.path == '/api/password-reset/confirm':
                self.server.store.reset_password(body)
                self.respond({'ok': True})
                return
            if self.path == '/api/verify-email':
                self.server.store.confirm_email(body.get('token'))
                self.respond({'ok': True})
                return
            if self.path in ('/api/login', '/api/register'):
                delivery_result = None
                if self.path == '/api/register':
                    if body.get('password') != body.get('password_confirmation'):
                        raise Problem('Las contraseñas no coinciden.')
                    uid = self.server.store.register(body)
                    delivery_result = self.send_email_verification(uid)
                token = self.server.store.login(body)
                secure = '; Secure' if getattr(self.server, 'secure_cookie', False) else ''
                self.respond({'ok': True, 'email_verification': delivery_result}, cookie=f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}')
                return
            user = self.server.store.session(self.cookie())
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), user['csrf']):
                raise Problem('Recarga la página antes de continuar.', 403)
            store = self.server.store
            if self.path == '/api/microsoft/link':
                self.start_microsoft(self.cookie())
                return
            if self.path == '/api/google/link':
                self.start_google(self.cookie())
                return
            if self.path == '/api/logout':
                with store.connection(True) as c:
                    c.execute('DELETE FROM sessions WHERE token=?', (self.cookie(),))
                secure = '; Secure' if getattr(self.server, 'secure_cookie', False) else ''
                self.respond({'ok': True}, cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' + secure)
                return
            elif self.path == '/api/email-verification/resend':
                self.respond(self.send_email_verification(user['id']))
                return
            elif self.path == '/api/profile':
                result = store.update_profile(user['id'], body)
            elif self.path == '/api/packages':
                if getattr(getattr(self.server,'webpay',None),'mode','disabled') != 'disabled':
                    raise Problem('Utiliza Webpay para comprar este plan.',409)
                result = store.buy(user['id'], body)
            elif self.path == '/api/discounts/quote':
                with store.connection() as c:result=store.discount_quote(c,body.get('catalog_id'),body.get('discount_code',''))
                self.respond(result);return
            elif self.path == '/api/admin/discounts':
                self.admin(user)
                self.respond(store.save_discount(user['id'],body));return
            elif self.path == '/api/webpay/create':
                service = getattr(self.server,'webpay',None)
                if not service:
                    raise PaymentError('Webpay no está habilitado.',409)
                self.respond(service.create(user['id'],body.get('catalog_id'),body.get('idempotency_key'),body.get('rules_acceptance'),body.get('discount_code',''),body.get('expected_price'),body.get('resume_payment_id')))
                return
            elif self.path == '/api/webpay/abandon':
                service=getattr(self.server,'webpay',None)
                if not service:raise PaymentError('Webpay no está habilitado.',409)
                self.respond(service.abandon(user['id'],body.get('id')))
                return
            elif self.path == '/api/webpay/reconcile':
                service = getattr(self.server,'webpay',None)
                if not service or type(body.get('id')) is not int:
                    raise PaymentError('Pago inválido.',400)
                self.respond(service.reconcile(body['id'],uid=user['id']))
                return
            elif self.path == '/api/admin/management-access':
                self.admin(user)
                self.respond(store.set_management_access(user['id'],body));return
            elif self.path == '/api/admin/users/access':
                self.admin(user)
                self.respond(store.manage_user_access(user['id'],body));return
            elif self.path == '/api/admin/webpay/live-sales':
                self.admin(user)
                self.respond(self.server.webpay.set_live_sales(user['id'],body));return
            elif self.path == '/api/admin/webpay/settings':
                self.admin(user)
                service = getattr(self.server,'webpay',None)
                if not service:
                    raise PaymentError('La configuración de Webpay no está disponible.',503)
                self.respond(service.configure(user['id'],body))
                return
            elif self.path == '/api/admin/webpay/reconcile':
                self.admin(user)
                service = getattr(self.server,'webpay',None)
                if not service or type(body.get('id')) is not int:
                    raise PaymentError('Pago inválido.',400)
                self.respond(service.reconcile(body['id']))
                return
            elif self.path == '/api/admin/session-adjustments':
                self.admin(user)
                self.respond(store.adjust_sessions(user['id'],body))
                return
            elif self.path == '/api/admin/packages/archive':
                self.admin(user)
                self.respond(store.archive_package(user['id'],body))
                return
            elif self.path in ('/api/admin/daily-contacts/skedu/preview','/api/admin/daily-contacts/skedu/import'):
                self.admin(user)
                self.respond(store.import_skedu_contacts(user['id'],body,preview=self.path.endswith('/preview')))
                return
            elif self.path == '/api/admin/daily-contacts/skedu/remove':
                self.admin(user)
                self.respond(store.remove_skedu_contacts(user['id'],body))
                return
            elif self.path == '/api/admin/manual-credits':
                self.admin(user,management=True)
                self.respond(store.manual_credit(user['id'],body))
                return
            elif self.path == '/api/packages/accept-rules':
                self.respond(store.accept_manual_rules(user['id'],body))
                return
            elif self.path == '/api/bookings/batch':
                if not validate_acceptance(body.get('rules_acceptance')):raise Problem('Debes leer y aceptar las normas vigentes antes de reservar.',400)
                self.respond(store.reserve_batch(user['id'],body))
                return
            elif self.path == '/api/bookings':
                if not validate_acceptance(body.get('rules_acceptance')):raise Problem('Debes leer y aceptar las normas vigentes antes de reservar.',400)
                result = store.reserve(user['id'], body)
            elif self.path.startswith('/api/bookings/'):
                if not validate_acceptance(body.get('rules_acceptance')):raise Problem('Debes leer y aceptar las normas vigentes antes de reservar.',400)
                try:
                    bid = int(self.path.rsplit('/', 1)[-1])
                except ValueError:
                    raise Problem('Reserva inválida.')
                result = store.modify(user['id'], bid, body)
            elif self.path == '/api/documents':
                result = store.upload(user['id'], body)
            elif self.path in ('/api/admin/migrations/preview', '/api/admin/migrations/import'):
                self.admin(user)
                result = store.import_legacy_csv(user['id'], body, preview=self.path.endswith('/preview'))
                self.respond(result)
                return
            elif self.path == '/api/admin/email/migration-preview':
                self.admin(user)
                if user['email'] != ADMIN_EMAIL or user['role'] != 'admin':
                    raise Problem('Acceso exclusivo del administrador propietario.',403)
                delivery=getattr(self.server,'email_delivery',None)
                if not delivery or not delivery.configured or delivery.provider!='resend':
                    raise Problem('Resend no está configurado para esta prueba.',503)
                if body.get('confirmation') != 'ENVIAR PRUEBA VALHALLA':
                    raise Problem('Confirmación de prueba inválida.',400)
                from html import escape
                target='clubdeamixgpt@gmail.com'
                title='[PRUEBA CORPORATIVA] Valhalla Office | Tus horas te esperan'
                plain=('¡Tenemos nueva plataforma!\\n\\nTus horas ya están cargadas.\\nTe esperamos para seguir agendando.\\n\\n'
                       'https://www.valhallaoffice.cl\\n\\n¿Cómo acceder a tus horas?\\n\\n'
                       'Ingresa con tu cuenta de Google, usando el mismo correo que tenías registrado en Skedu.\\n\\n'
                       'Si no tienes una cuenta de Google asociada a ese correo, crea un nuevo usuario con el mismo correo que utilizabas en Skedu.\\n\\n'
                       'Al ingresar, encontrarás tus horas pendientes de agendar para que puedas seguir utilizándolas.\\n\\n'
                       'Desde ahora, realiza tus nuevas reservas en esta plataforma.\\n\\n'
                       '¿Necesitas ayuda? Escríbenos a contacto@valhallaoffice.cl\\n\\n'
                       'Sigue tu vocación.\\nNosotros tenemos el espacio.')
                html='''<!doctype html><html lang="es"><head><meta charset="utf-8"></head><body style="margin:0;background:#f4f6ef;font-family:Arial,sans-serif;color:#193e37;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:28px 12px;"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:580px;background:white;border:1px solid #dce4d7;border-radius:16px;"><tr><td align="center" style="padding:28px 24px 12px;"><strong style="font-size:26px;letter-spacing:2px;">VALHALLA OFFICE</strong><p style="font-size:12px;letter-spacing:2px;">ESPACIOS PARA PROFESIONALES</p></td></tr><tr><td style="padding:12px 32px 30px;"><h1 style="text-align:center;font-size:26px;">¡Tenemos nueva plataforma!</h1><p style="text-align:center;font-size:17px;line-height:1.65;">Tus horas ya están cargadas.<br>Te esperamos para seguir agendando.</p><table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" bgcolor="#16636b" style="border-radius:9px;"><a href="https://www.valhallaoffice.cl" style="display:block;padding:18px;color:white;text-decoration:none;font-weight:bold;font-size:17px;">www.valhallaoffice.cl</a></td></tr></table><h2 style="font-size:19px;text-align:center;margin-top:30px;">¿Cómo acceder a tus horas?</h2><p style="line-height:1.65;text-align:justify;">Ingresa con tu cuenta de Google, usando el mismo correo que tenías registrado en Skedu.</p><p style="line-height:1.65;text-align:justify;">Si no tienes una cuenta de Google asociada a ese correo, crea un nuevo usuario con el mismo correo que utilizabas en Skedu.</p><p style="line-height:1.65;text-align:justify;">Al ingresar, encontrarás tus horas pendientes de agendar para que puedas seguir utilizándolas.</p><p style="line-height:1.65;text-align:justify;">Desde ahora, realiza tus nuevas reservas en esta plataforma.</p><p style="text-align:center;margin-top:30px;">¿Necesitas ayuda? Escríbenos a<br><a href="mailto:contacto@valhallaoffice.cl">contacto@valhallaoffice.cl</a></p><p style="text-align:center;font-weight:bold;margin-top:30px;">Sigue tu vocación.<br>Nosotros tenemos el espacio.</p></td></tr></table></td></tr></table></body></html>'''
                delivery.send_message(target,title,plain,idempotency_key='valhalla-migration-preview-clubdeamix-20261025',html=html)
                self.respond({'ok':True,'message':'Resend aceptó la prueba para clubdeamixgpt@gmail.com. Revisa su recepción.'})
                return
            elif self.path == '/api/admin/email/test':
                self.admin(user)
                delivery=getattr(self.server,'email_delivery',None)
                if not delivery or not delivery.configured:
                    raise Problem('Configura el proveedor, remitente, URL HTTPS y clave en Render antes de probar.',503)
                sent=self.deliver_email('test',user['id'],ADMIN_EMAIL)
                self.respond({'ok':sent,'message':'El proveedor aceptó la prueba. Revisa entrada y spam; aceptación no garantiza entrega.' if sent else 'La prueba falló. Revisa el diagnóstico de correo en administración.'})
                return
            elif self.path == '/api/admin/migrations':
                self.admin(user)
                result = store.import_legacy(user['id'], body)
            elif self.path == '/api/admin':
                self.admin(user)
                result = store.admin_action(user['id'], body)
            else:
                raise Problem('Ruta no encontrada.', 404)
            self.respond({'ok': True, 'id': result})
        except (Problem, PaymentError) as e:
            self.respond({'error': e.message}, e.status)
        except sqlite3.Error:
            self.respond({'error': 'No se pudo completar la operación. Inténtalo nuevamente.'}, 503)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--db', default=os.environ.get('VALHALLA_DB_PATH', str(ROOT / 'data' / 'valhalla.sqlite3')))
    parser.add_argument('--seed-demo', action='store_true', help=argparse.SUPPRESS)  # Accepted for old launch commands; no accounts are seeded.
    parser.add_argument('--secure-cookie', action='store_true', help='Require HTTPS for session cookies (hosting behind TLS proxy).')
    args = parser.parse_args()
    store = Store(args.db)
    try:
        store.configure_access(os.environ.get('VALHALLA_ADMIN_PASSWORD'))
    except ValueError as error:
        parser.error(str(error))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.store = store
    server.secure_cookie = args.secure_cookie
    server.email_delivery = EmailDelivery()
    server.google = google_auth.GoogleOAuth()
    server.microsoft = microsoft_auth.MicrosoftOAuth()
    try:
        server.webpay = PaymentService(store, WebpayGateway())
        server.webpay.restore_configuration()
    except ValueError as error:
        parser.error(str(error))
    stop_background = threading.Event()
    notifier = NotificationDispatcher(store,server.email_delivery)
    def send_notifications():
        while not stop_background.is_set():
            try:
                notifier.dispatch_pending()
            except sqlite3.Error:
                pass  # Reservation and queue remain stored; expired leases can be retried.
            stop_background.wait(1)
    threading.Thread(target=send_notifications,daemon=True).start()
    def reconcile_payments():
        while not stop_background.is_set():
            try:
                server.webpay.reconcile_pending()
            except sqlite3.Error:
                pass  # Keep pending attempts for the next pass; never credit on database failure.
            stop_background.wait(30)
    threading.Thread(target=reconcile_payments, daemon=True).start()
    # Daily TTLock integration is opt-in and OFF unless TTLOCK_ENABLED=true.
    from ttlock_worker import start_worker
    start_worker(args.db, stop_background)
    print(f'Valhalla Office · prototipo local · puerto {args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_background.set()
        server.server_close()


if __name__ == '__main__':
    main()
