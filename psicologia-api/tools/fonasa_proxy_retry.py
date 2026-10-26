"""Prueba aislada del túnel HTTPS mediante proxy: tres intentos de hasta 30 s.
No utiliza credenciales ni envía datos de pacientes. Ejecutar manualmente en Shell.
"""
import socket
import ssl
import time

PROXY_HOST="45.181.123.185"
PROXY_PORT=999
TARGET="bonowebptd.fonasa.cl"

def try_once(number):
    started=time.monotonic()
    print(f"Intento {number}/3 iniciado",flush=True)
    try:
        with socket.create_connection((PROXY_HOST,PROXY_PORT),timeout=30) as sock:
            connected=time.monotonic()
            print(f"  Proxy TCP conectado en {connected-started:.2f}s",flush=True)
            sock.settimeout(30)
            sock.sendall((f"CONNECT {TARGET}:443 HTTP/1.1\r\nHost: {TARGET}:443\r\n\r\n").encode("ascii"))
            response=b""
            while b"\r\n\r\n" not in response and len(response)<8192:
                piece=sock.recv(1024)
                if not piece: break
                response+=piece
            status=response.split(b"\r\n",1)[0].decode("ascii","replace")
            print(f"  CONNECT: {status[:100]} ({time.monotonic()-started:.2f}s)",flush=True)
            if not status.startswith(("HTTP/1.1 200","HTTP/1.0 200")):
                return False
            if response.partition(b"\r\n\r\n")[2]:
                print("  El proxy envió datos adicionales; no se prueba TLS.",flush=True)
                return False
            with ssl.create_default_context().wrap_socket(sock,server_hostname=TARGET) as tls:
                print(f"  TLS verificado: {tls.version()} ({time.monotonic()-started:.2f}s)",flush=True)
                return True
    except Exception as exc:
        print(f"  Falló: {type(exc).__name__} ({time.monotonic()-started:.2f}s)",flush=True)
    return False

if __name__=="__main__":
    successes=0
    for number in range(1,4):
        successes+=int(try_once(number))
        if number<3:
            time.sleep(2)
    print(f"Resultado: {successes}/3 intentos con TLS verificado",flush=True)
