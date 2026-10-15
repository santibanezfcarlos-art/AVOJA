"""Run the browser flow against a disposable server with a generated private password."""
import os
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        private = secrets.token_urlsafe(32)
        env['VALHALLA_ADMIN_PASSWORD'] = private
        with open(Path(directory) / 'server.log', 'w') as log:
            server = subprocess.Popen(
                ['python3', 'app.py', '--db', str(Path(directory) / 'test.sqlite3'), '--port', str(port)],
                cwd=root, env=env, stdout=log, stderr=log)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/', timeout=1):
                            break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError('No se pudo iniciar el servidor de pruebas.')
                        time.sleep(.1)
                else:
                    raise RuntimeError('El servidor de pruebas no respondió.')
                env['VALHALLA_TEST_URL'] = f'http://127.0.0.1:{port}'
                env['VALHALLA_TEST_ADMIN_PASSWORD'] = private
                return subprocess.run(['node', 'tests/browser.cjs'], cwd=root, env=env).returncode
            finally:
                server.terminate()
                server.wait(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
