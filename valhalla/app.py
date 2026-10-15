"""Valhalla Office: prototipo local, sin dependencias ni cobros reales."""
import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CHILE = ZoneInfo('America/Santiago')
CATALOG = {'5': (5, 45000), '10': (10, 85000), '20': (20, 160000)}
MAX_BODY = 3_000_000
ADMIN_EMAIL = "santibanezf.carlos@gmail.com"
LEGACY_DEMO_EMAILS = ("admin@valhalla.demo", "profesional@valhalla.demo")


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def supports_professional(user):
    return user and (user['role'] == 'professional' or (user['role'] == 'admin' and user['email'] == ADMIN_EMAIL))


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='seconds')


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
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
              password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'professional',
              specialty TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              filename TEXT NOT NULL, mime TEXT NOT NULL, content BLOB NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
              reviewer_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS packages (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
              hours INTEGER NOT NULL, remaining INTEGER NOT NULL CHECK(remaining >= 0),
              price INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL);
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
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY, actor_id INTEGER REFERENCES users(id),
              action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

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
                        c.execute("UPDATE users SET password=?,role='admin',active=1,verified=1 WHERE id=?",
                                  (password_hash(admin_password), owner['id']))
                else:
                    c.execute("INSERT INTO users(name,email,password,role,verified) VALUES(?,?,?,'admin',1)",
                              ('Administración Valhalla', ADMIN_EMAIL, password_hash(admin_password)))

    def session(self, token):
        with self.connection() as c:
            row = c.execute('SELECT u.*,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?',
                            (token, time.time())).fetchone()
            if not row or not row['active'] or row['email'] in LEGACY_DEMO_EMAILS or (row['role'] == 'admin' and row['email'] != ADMIN_EMAIL):
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
            if not password_matches(password, encoded) or not row or not row['active'] or row['email'] in LEGACY_DEMO_EMAILS or (row['role'] == 'admin' and row['email'] != ADMIN_EMAIL):
                raise Problem('Correo o contraseña incorrectos, o cuenta desactivada.', 401)
            if body.get('portal') == 'admin' and (row['role'] != 'admin' or row['email'] != ADMIN_EMAIL):
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
        if not 2 <= len(name) <= 100 or len(email) > 200 or '@' not in email or '.' not in email.split('@')[-1]:
            raise Problem('Ingresa un nombre y un correo válidos.')
        if not 10 <= len(password) <= 200:
            raise Problem('La contraseña debe tener entre 10 y 200 caracteres.')
        if not 2 <= len(specialty) <= 100:
            raise Problem('Indica tu profesión o especialidad.')
        try:
            with self.connection(True) as c:
                cursor = c.execute('INSERT INTO users(name,email,password,specialty) VALUES(?,?,?,?)',
                                   (name, email, password_hash(password), specialty))
                self.log(c, cursor.lastrowid, 'registered', cursor.lastrowid)
        except sqlite3.IntegrityError:
            raise Problem('Ya existe una cuenta con ese correo.', 409)

    def state(self, user):
        with self.connection() as c:
            profile = {k: user[k] for k in ('id', 'name', 'email', 'role', 'specialty', 'verified', 'active')}
            return {'user': profile, 'csrf': user['csrf'], 'today': now().astimezone(CHILE).date().isoformat(),
                    'packages': [dict(r) for r in c.execute('SELECT * FROM packages WHERE user_id=? ORDER BY id DESC', (user['id'],))],
                    'bookings': [dict(r) for r in c.execute('SELECT * FROM bookings WHERE user_id=? ORDER BY start', (user['id'],))],
                    'documents': [dict(r) for r in c.execute('SELECT id,filename,status,note,created_at FROM documents WHERE user_id=? ORDER BY id DESC', (user['id'],))],
                    'catalog': [{'id': key, 'hours': val[0], 'price': val[1]} for key, val in CATALOG.items()]}

    def update_profile(self, uid, body):
        name = str(body.get('name', '')).strip()
        specialty = str(body.get('specialty', '')).strip()
        if not 2 <= len(name) <= 100 or not 2 <= len(specialty) <= 100:
            raise Problem('Indica tu nombre y especialidad (entre 2 y 100 caracteres).')
        with self.connection(True) as c:
            c.execute('UPDATE users SET name=?,specialty=? WHERE id=?', (name, specialty, uid))
            self.log(c, uid, 'profile_updated', uid)

    def parse_slot(self, value):
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
            local = parsed.astimezone(CHILE)
            if local.minute or local.second or local.microsecond or not 8 <= local.hour < 20:
                raise ValueError()
            if parsed <= now() or parsed > now() + timedelta(days=90):
                raise ValueError()
            return stamp(parsed)
        except (ValueError, TypeError):
            raise Problem('Selecciona una hora futura entre 08:00 y 20:00, dentro de los próximos 90 días.')

    def availability(self, date):
        try:
            day = datetime.strptime(date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            raise Problem('Fecha inválida.')
        today = now().astimezone(CHILE).date()
        if day < today or day > today + timedelta(days=90):
            raise Problem('Consulta fechas de hoy a los próximos 90 días.')
        with self.connection() as c:
            occupied = {(r['box'], r['start']) for r in c.execute("SELECT box,start FROM bookings WHERE status='confirmed'")}
        result = []
        for hour in range(8, 20):
            start = datetime(day.year, day.month, day.day, hour, tzinfo=CHILE)
            result.append({'label': f'{hour:02d}:00', 'start': stamp(start),
                           'boxes': [{'box': box, 'available': (box, stamp(start)) not in occupied and start > now()} for box in range(1, 4)]})
        return {'date': date, 'slots': result}

    def professional(self, c, uid):
        row = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not row or not supports_professional(row) or not row['active'] or not row['verified']:
            raise Problem('Para reservar necesitas una cuenta profesional activa y documentación aprobada.', 403)

    def buy(self, uid, body):
        key = str(body.get('catalog_id', ''))
        if key not in CATALOG:
            raise Problem('Paquete inválido.')
        hours, price = CATALOG[key]
        with self.connection(True) as c:
            row = c.execute('SELECT role,email,active FROM users WHERE id=?', (uid,)).fetchone()
            if not row or not supports_professional(row) or not row['active']:
                raise Problem('Solo los profesionales activos pueden solicitar paquetes.', 403)
            cur = c.execute('INSERT INTO packages(user_id,hours,remaining,price,created_at) VALUES(?,?,?,?,?)',
                            (uid, hours, hours, price, stamp(now())))
            self.log(c, uid, 'payment_requested_simulated', cur.lastrowid)
            return cur.lastrowid

    def reserve(self, uid, body):
        start = self.parse_slot(body.get('start'))
        box = body.get('box')
        if type(box) is not int or box not in (1, 2, 3):
            raise Problem('Box inválido.')
        try:
            with self.connection(True) as c:
                self.professional(c, uid)
                if c.execute("SELECT id FROM bookings WHERE user_id=? AND start=? AND status='confirmed'", (uid, start)).fetchone():
                    raise Problem('Ya tienes una reserva a esa hora.', 409)
                package = c.execute("SELECT * FROM packages WHERE user_id=? AND status='paid' AND remaining>0 ORDER BY id LIMIT 1", (uid,)).fetchone()
                if not package:
                    raise Problem('Necesitas saldo de un paquete con pago confirmado.', 409)
                cur = c.execute('INSERT INTO bookings(user_id,package_id,box,start,created_at) VALUES(?,?,?,?,?)',
                                (uid, package['id'], box, start, stamp(now())))
                c.execute('UPDATE packages SET remaining=remaining-1 WHERE id=?', (package['id'],))
                self.log(c, uid, 'reserved', cur.lastrowid)
                return cur.lastrowid
        except sqlite3.IntegrityError:
            raise Problem('Ese box acaba de ser reservado. Elige otro horario.', 409)

    def modify(self, uid, bid, body):
        start = self.parse_slot(body.get('start'))
        box = body.get('box')
        if type(box) is not int or box not in (1, 2, 3):
            raise Problem('Box inválido.')
        try:
            with self.connection(True) as c:
                self.professional(c, uid)
                booking = c.execute('SELECT * FROM bookings WHERE id=? AND user_id=?', (bid, uid)).fetchone()
                if not booking:
                    raise Problem('Reserva no encontrada.', 404)
                if booking['status'] != 'confirmed' or booking['modifications'] >= 1:
                    raise Problem('Esta reserva ya no admite modificaciones.', 409)
                cutoff = now() + timedelta(hours=2)
                if datetime.fromisoformat(booking['start']) < cutoff or datetime.fromisoformat(start) < cutoff:
                    raise Problem('El cambio requiere al menos dos horas de anticipación para ambos horarios.', 409)
                if booking['box'] == box and booking['start'] == start:
                    raise Problem('Elige un box u horario diferente; tu cambio sigue disponible.')
                if c.execute("SELECT id FROM bookings WHERE user_id=? AND start=? AND status='confirmed' AND id<>?", (uid, start, bid)).fetchone():
                    raise Problem('Ya tienes una reserva a esa hora.', 409)
                c.execute('UPDATE bookings SET box=?,start=?,modifications=modifications+1 WHERE id=?', (box, start, bid))
                self.log(c, uid, 'modified', bid)
        except sqlite3.IntegrityError:
            raise Problem('Ese horario ya está ocupado. Tu reserva original sigue vigente.', 409)

    def upload(self, uid, body):
        filename = Path(str(body.get('filename', ''))).name[:150]
        try:
            raw = base64.b64decode(body.get('content', ''), validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise Problem('Archivo inválido.')
        # The prototype accepts PDF only; files are stored privately and downloaded as attachments.
        if not filename.lower().endswith('.pdf') or not raw.startswith(b'%PDF-') or not 5 < len(raw) <= 2_000_000:
            raise Problem('Adjunta un PDF de hasta 2 MB.')
        with self.connection(True) as c:
            role = c.execute('SELECT role,email FROM users WHERE id=?', (uid,)).fetchone()
            if not supports_professional(role):
                raise Problem('Solo profesionales pueden adjuntar documentos.', 403)
            c.execute("INSERT INTO documents(user_id,filename,mime,content,created_at) VALUES(?,?,'application/pdf',?,?)", (uid, filename, raw, stamp(now())))
            c.execute('UPDATE users SET verified=0 WHERE id=?', (uid,))
            self.log(c, uid, 'document_uploaded', uid)

    def admin_state(self):
        with self.connection() as c:
            return {'users': [dict(r) for r in c.execute('SELECT id,name,email,specialty,role,verified,active FROM users ORDER BY id')],
                    'documents': [dict(r) for r in c.execute('SELECT d.id,d.user_id,d.filename,d.status,d.note,d.created_at,u.name FROM documents d JOIN users u ON u.id=d.user_id ORDER BY d.id DESC')],
                    'packages': [dict(r) for r in c.execute('SELECT p.*,u.name FROM packages p JOIN users u ON u.id=p.user_id ORDER BY p.id DESC')],
                    'bookings': [dict(r) for r in c.execute('SELECT b.*,u.name FROM bookings b JOIN users u ON u.id=b.user_id ORDER BY b.start')],
                    'audit': [dict(r) for r in c.execute('SELECT a.*,u.name FROM audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 30')]}

    def admin_action(self, actor, body):
        action, target = body.get('action'), body.get('id')
        if type(target) is not int:
            raise Problem('Identificador inválido.')
        with self.connection(True) as c:
            if action == 'payment':
                row = c.execute('SELECT * FROM packages WHERE id=?', (target,)).fetchone()
                if not row:
                    raise Problem('Paquete no encontrado.', 404)
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
                latest = c.execute('SELECT MAX(id) AS id FROM documents WHERE user_id=?', (row['user_id'],)).fetchone()['id']
                if target != latest:
                    raise Problem('Revisa el documento más reciente de este profesional.', 409)
                decision = body.get('decision')
                note = str(body.get('note', '')).strip()[:500]
                if decision not in ('approved', 'rejected') or (decision == 'rejected' and not note):
                    raise Problem('Indica una decisión y, si rechazas, un motivo.')
                c.execute('UPDATE documents SET status=?,note=?,reviewer_id=? WHERE id=?', (decision, note, actor, target))
                c.execute('UPDATE users SET verified=? WHERE id=?', (int(decision == 'approved'), row['user_id']))
            elif action == 'user':
                row = c.execute('SELECT * FROM users WHERE id=?', (target,)).fetchone()
                if not row or row['role'] == 'admin':
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
            else:
                raise Problem('Acción inválida.')
            self.log(c, actor, 'admin_' + action + '_' + str(body.get('decision', '')), target)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ValhallaPrototype'

    def headers_common(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

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

    def admin(self, user):
        if user['role'] != 'admin' or user['email'] != ADMIN_EMAIL:
            raise Problem('Acceso exclusivo de administración.', 403)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        route = urlparse(self.path)
        try:
            if not route.path.startswith('/api/'):
                files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
                if route.path not in files:
                    raise Problem('Página no encontrada.', 404)
                filename, mime = files[route.path]
                raw = (ROOT / 'static' / filename).read_bytes()
                self.send_response(200)
                self.headers_common()
                self.send_header('Content-Type', mime + '; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            user = self.server.store.session(self.cookie())
            if route.path == '/api/me':
                self.respond(self.server.store.state(user))
            elif route.path == '/api/availability':
                self.respond(self.server.store.availability(parse_qs(route.query).get('date', [''])[0]))
            elif route.path == '/api/admin':
                self.admin(user)
                self.respond(self.server.store.admin_state())
            elif route.path.startswith('/api/documents/'):
                try:
                    did = int(route.path.rsplit('/', 1)[-1])
                except ValueError:
                    raise Problem('Documento no encontrado.', 404)
                with self.server.store.connection() as c:
                    doc = c.execute('SELECT * FROM documents WHERE id=?', (did,)).fetchone()
                if not doc or (user['role'] != 'admin' and user['id'] != doc['user_id']):
                    raise Problem('Documento no encontrado.', 404)
                self.send_response(200)
                self.headers_common()
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Disposition', f'attachment; filename="documento-{did}.pdf"')
                self.send_header('Content-Length', str(len(doc['content'])))
                self.end_headers()
                self.wfile.write(doc['content'])
            else:
                raise Problem('Ruta no encontrada.', 404)
        except Problem as e:
            self.respond({'error': e.message}, e.status)

    def do_POST(self):
        try:
            # JSON-only public endpoints and same-origin checks prevent login CSRF.
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise Problem('Se requiere JSON.', 415)
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://' + self.headers.get('Host', ''), 'https://' + self.headers.get('Host', '')):
                raise Problem('Origen no permitido.', 403)
            body = self.body()
            if self.path in ('/api/login', '/api/register'):
                if self.path == '/api/register':
                    self.server.store.register(body)
                token = self.server.store.login(body)
                secure = '; Secure' if getattr(self.server, 'secure_cookie', False) else ''
                self.respond({'ok': True}, cookie=f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}')
                return
            user = self.server.store.session(self.cookie())
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token', ''), user['csrf']):
                raise Problem('Recarga la página antes de continuar.', 403)
            store = self.server.store
            if self.path == '/api/logout':
                with store.connection(True) as c:
                    c.execute('DELETE FROM sessions WHERE token=?', (self.cookie(),))
                secure = '; Secure' if getattr(self.server, 'secure_cookie', False) else ''
                self.respond({'ok': True}, cookie='session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0' + secure)
                return
            elif self.path == '/api/profile':
                result = store.update_profile(user['id'], body)
            elif self.path == '/api/packages':
                result = store.buy(user['id'], body)
            elif self.path == '/api/bookings':
                result = store.reserve(user['id'], body)
            elif self.path.startswith('/api/bookings/'):
                try:
                    bid = int(self.path.rsplit('/', 1)[-1])
                except ValueError:
                    raise Problem('Reserva inválida.')
                result = store.modify(user['id'], bid, body)
            elif self.path == '/api/documents':
                result = store.upload(user['id'], body)
            elif self.path == '/api/admin':
                self.admin(user)
                result = store.admin_action(user['id'], body)
            else:
                raise Problem('Ruta no encontrada.', 404)
            self.respond({'ok': True, 'id': result})
        except Problem as e:
            self.respond({'error': e.message}, e.status)
        except sqlite3.Error:
            self.respond({'error': 'No se pudo completar la operación. Inténtalo nuevamente.'}, 503)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'valhalla.sqlite3'))
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
    print(f'Valhalla Office · prototipo local · puerto {args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == '__main__':
    main()
