"""Local outbound-only agent. No cookies uploaded; no listening port.

The initial release consumes preview jobs only. It never passes final-issuance
arguments to Playwright. Admin password is prompted, used once and not stored.
"""
import argparse
import base64
import getpass
import json
import ssl
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

BASE = 'https://psicologia-carlos-calendar-api.onrender.com'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call(path, body, auth):
    req = Request(BASE + path, data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type':'application/json', 'Authorization':auth,
        'X-Billing-Action':'manual-invoice'})
    with build_opener(NoRedirect).open(req, timeout=30) as response:
        return json.loads(response.read(1000000))


def connection_error(exc):
    """Only allowlisted diagnostics: never echo exception text, URLs or bodies."""
    if isinstance(exc, HTTPError):
        messages = {
            401: 'Usuario o contraseña del panel rechazados. Revisa ambos; no uses la clave SII ni el HASH de Render.',
            403: 'El servidor rechazó la autorización del agente (HTTP 403).',
            404: 'El servidor no encuentra la conexión del agente (HTTP 404). Revisa el despliegue.',
            429: 'Demasiados intentos. Espera antes de volver a conectar.',
            503: 'El servicio o el almacenamiento del panel no está disponible (HTTP 503).',
        }
        return messages.get(exc.code, 'El servidor respondió HTTP ' + str(exc.code) + '. No se inició la preparación.')
    reason = exc.reason if isinstance(exc, URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return 'No se pudo verificar la conexión HTTPS. Revisa fecha/hora de Windows y certificados; no desactives la verificación TLS.'
    if isinstance(reason, TimeoutError):
        return 'La conexión al panel agotó el tiempo de espera. Intenta nuevamente.'
    if isinstance(exc, (URLError, OSError)):
        return 'No se pudo establecer conexión de red con el panel. Revisa Internet y vuelve a intentar.'
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return 'El panel devolvió una respuesta no reconocida. No se inició la preparación.'
    return 'Fallo interno del agente. No se inició la preparación; solicita revisión del script.'


def run_job(job, profile):
    if job.get('mode') != 'preview':
        return {'status':'blocked', 'emitted':False}
    # Temporary data are outside the repository; removed even on child failure.
    with tempfile.TemporaryDirectory(prefix='psc-sii-') as tmp:
        folder = Path(tmp)
        data_path, result_path = folder/'datos.json', folder/'resultado.json'
        data_path.write_text(json.dumps(job['data']), encoding='utf-8')
        data_path.chmod(0o600)
        command = [sys.executable, str(Path(__file__).with_name('sii_monto_local.py')),
            '--perfil',str(profile), '--monto',str(job['data']['monto']),
            '--continuar','--mostrar-campos','--datos',str(data_path),'--resultado',str(result_path)]
        try:
            # Do not stream browser tracebacks (may contain entered field values).
            proc = subprocess.run(command, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = json.loads(result_path.read_text()) if result_path.exists() else {}
            if proc.returncode != 0:
                result = {'status':'failed'}
        except (subprocess.TimeoutExpired, ValueError, OSError):
            result = {'status':'failed'}
        status = result.get('status','failed')
        if status not in {'prepared','blocked','session_required','failed'}:
            status = 'blocked'
        return {'status':status, 'emitted':False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--perfil', type=Path, required=True)
    ap.add_argument('--usuario', help='Usuario del panel; si se omite se solicita en consola')
    args = ap.parse_args()
    profile = args.perfil.expanduser().resolve()
    if not profile.is_dir():
        ap.error('No existe el perfil. Ejecuta primero sii_sesion_persistente.py autorizar.')
    username = args.usuario or input('Usuario del PANEL PSC [carlos]: ').strip() or 'carlos'
    print('La contraseña se escribe sin mostrar letras ni asteriscos. Al terminar pulsa Enter.')
    password = getpass.getpass('Contraseña del PANEL PSC (no la del SII): ')
    if not password:
        print('No se ingresó contraseña. Vuelve a ejecutar el comando.')
        return
    basic = base64.b64encode((username+':'+password).encode()).decode()
    try:
        token = call('/admin/boletas/sii-agent-token', {}, 'Basic '+basic)['token']
        if not isinstance(token, str) or not token:
            raise ValueError('invalid_token')
    finally:
        password = basic = None
    auth = 'Bearer '+token
    print('Agente conectado. Modo preparación: NUNCA pulsa EMITIR final. Ctrl+C para salir.')
    pending = None
    while True:
        try:
            if pending is not None:
                call('/api/sii-agent/jobs/'+pending[0]+'/result', pending[1], auth)
                print('Trabajo finalizado:', pending[1]['status'])
                if pending[1]['status'] == 'session_required':
                    print('Sesión SII vencida. Detén el agente y vuelve a autorizar manualmente.')
                    return
                pending = None
            job = call('/api/sii-agent/claim', {}, auth).get('job')
            if job:
                print('Preparando compra:', job['data']['order_id'])
                pending = (job['id'], run_job(job, profile))
                continue
        except HTTPError as exc:
            if exc.code in (401,403,409):
                print('Agente detenido. Revisa acceso/trabajo en el panel. No se repetirá el formulario.')
                return
            print('Servidor no disponible. Reintentando solo comunicación, no el formulario.')
        except (OSError, ValueError):
            print('Sin conexión. Reintentando solo comunicación, no el formulario.')
        time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nAgente detenido. Si quedó un trabajo activo, ciérralo desde el panel.')
    except Exception as exc:
        print(connection_error(exc))
        sys.exit(1)
