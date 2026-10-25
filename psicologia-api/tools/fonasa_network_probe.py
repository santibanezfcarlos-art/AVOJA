"""Diagnóstico de conectividad Fonasa, sin credenciales ni sesiones."""
import socket
import ssl
from flask import Flask, jsonify

app = Flask(__name__)
HOST = "bonowebptd.fonasa.cl"

@app.get("/")
def diagnostic():
    result = {"target": HOST, "dns_ipv4": [], "dns_ipv6": [], "tcp": [], "note": "Sin credenciales, sesiones ni datos personales"}
    for family, key in ((socket.AF_INET, "dns_ipv4"), (socket.AF_INET6, "dns_ipv6")):
        try:
            records = socket.getaddrinfo(HOST, 443, family=family, type=socket.SOCK_STREAM)
            result[key] = sorted({record[4][0] for record in records})
        except socket.gaierror as exc:
            result[key + "_error"] = "Sin respuestas o resolución fallida"
    for ip in result["dns_ipv4"][:5]:
        row = {"family": "IPv4", "ip": ip}
        try:
            with socket.create_connection((ip, 443), timeout=4) as sock:
                row["tcp"] = "connected"
                with ssl.create_default_context().wrap_socket(sock, server_hostname=HOST) as tls:
                    row["tls"] = tls.version()
        except (OSError, ssl.SSLError) as exc:
            row["tcp"] = type(exc).__name__
        result["tcp"].append(row)
    for ip in result["dns_ipv6"][:5]:
        row = {"family": "IPv6", "ip": ip}
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
                sock.settimeout(4)
                sock.connect((ip, 443, 0, 0))
                row["tcp"] = "connected"
                with ssl.create_default_context().wrap_socket(sock, server_hostname=HOST) as tls:
                    row["tls"] = tls.version()
        except (OSError, ssl.SSLError) as exc:
            row["tcp"] = type(exc).__name__
        result["tcp"].append(row)
    return jsonify(result)

@app.get("/compare")
def compare_https():
    """Prueba selectiva solo de conectividad TCP/TLS; sin HTTP ni credenciales."""
    hosts = ("bonowebptd.fonasa.cl", "www.fonasa.cl", "www.sii.cl", "www.google.com")
    results = {}
    for host in hosts:
        item = {"dns": "unavailable", "tcp": "not_attempted", "tls": "not_attempted"}
        try:
            ip = socket.getaddrinfo(host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM)[0][4][0]
            item["dns"] = "resolved"
            with socket.create_connection((ip, 443), timeout=4) as sock:
                item["tcp"] = "connected"
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls:
                    item["tls"] = "verified"
        except Exception as exc:
            item["error_class"] = type(exc).__name__
        results[host] = item
    return jsonify({"results": results, "note": "Solo TCP/TLS, sin credenciales ni solicitudes HTTP"})

@app.get("/proxy-test")
def proxy_test():
    """CONNECT sin credenciales a un proxy público fijo. No envía solicitudes HTTP a Fonasa."""
    proxy_host, proxy_port = "170.239.84.103", 3128
    result = {"proxy": f"{proxy_host}:{proxy_port}", "connect": "not_attempted",
              "fonasa_tunnel": "not_attempted", "note": "Prueba aislada: no se usan credenciales ni datos de pacientes"}
    try:
        with socket.create_connection((proxy_host, proxy_port), timeout=6) as sock:
            result["connect"] = "connected"
            sock.settimeout(8)
            sock.sendall(b"CONNECT bonowebptd.fonasa.cl:443 HTTP/1.1\\r\\nHost: bonowebptd.fonasa.cl:443\\r\\nProxy-Connection: close\\r\\n\\r\\n")
            data = sock.recv(512)
            status = data.split(b"\\r\\n", 1)[0].decode("ascii", "replace")
            result["fonasa_tunnel"] = status[:90]
    except OSError as exc:
        result["connect_error"] = type(exc).__name__
    return jsonify(result)

@app.get("/proxy-batch")
def proxy_batch():
    """Pruebas paralelas de túnel CONNECT, sin autenticación ni datos de pacientes."""
    from concurrent.futures import ThreadPoolExecutor
    candidates = [
      ("131.196.177.254",999),("146.83.118.9",80),
      ("152.230.215.123",80),("167.249.29.218",999),
      ("177.128.115.198",999),("179.61.98.3",999),
      ("181.74.82.91",999),("186.10.150.185",8080),
      ("190.102.233.100",999),("190.102.247.122",999),
      ("190.54.100.74",8080),("170.239.84.103",3128)
    ]
    def check(row):
        host, port = row
        out = {"proxy":f"{host}:{port}","tcp":"failed","tunnel":"not_attempted"}
        try:
            with socket.create_connection((host,port),timeout=3) as sock:
                out["tcp"]="connected"
                sock.settimeout(4)
                sock.sendall(b"CONNECT bonowebptd.fonasa.cl:443 HTTP/1.1\r\nHost: bonowebptd.fonasa.cl:443\r\n\r\n")
                header=sock.recv(128)
                out["tunnel"]=header.split(b"\r\n",1)[0].decode("ascii","replace")[:90]
        except OSError as exc:
            out["error"]=type(exc).__name__
        return out
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(check,candidates))
    return jsonify({"results":results,"note":"Sin usuarios, claves, sesiones ni bonos"})

@app.get("/proxy-batch2")
def proxy_batch2():
    """Probar candidatos chilenos frescos HTTP CONNECT y SOCKS5 sin credenciales."""
    from concurrent.futures import ThreadPoolExecutor
    candidates = [
        ("45.225.204.8",999,"http"),
        ("179.57.172.172",999,"http"),
        ("190.121.52.125",999,"http"),
        ("167.249.28.73",999,"http"),
        ("45.161.191.17",999,"http"),
        ("45.181.123.185",999,"http"),
        ("190.110.96.250",999,"http"),
    ]
    def check(entry):
        host,port,protocol=entry
        result={"proxy":f"{host}:{port}","protocol":protocol,"result":"unreachable"}
        try:
            with socket.create_connection((host,port),timeout=3) as sock:
                result["result"]="tcp_connected"
                sock.settimeout(5)
                req=("CONNECT bonowebptd.fonasa.cl:443 HTTP/1.1\r\n"
                     "Host: bonowebptd.fonasa.cl:443\r\n"
                     "Proxy-Connection: close\r\n\r\n").encode("ascii")
                sock.sendall(req)
                data=sock.recv(150)
                result["result"]=data.split(b"\r\n",1)[0].decode("ascii","replace")[:90] or "empty_reply"
        except OSError as exc:
            result["error"]=type(exc).__name__
        return result
    with ThreadPoolExecutor(max_workers=7) as pool:
        results=list(pool.map(check,candidates))
    return jsonify({"results":results,"note":"Sin credenciales ni solicitudes autenticadas"})

@app.get("/proxy-tls")
def proxy_tls():
    """Confirmar CONNECT + TLS con verificación de hostname; sin credenciales."""
    host, port, target = "45.181.123.185", 999, "bonowebptd.fonasa.cl"
    result = {"proxy":f"{host}:{port}","connect":"not_attempted","tls":"not_attempted"}
    try:
        with socket.create_connection((host,port),timeout=7) as sock:
            sock.settimeout(12)
            sock.sendall(("CONNECT "+target+":443 HTTP/1.1\r\nHost: "+target+":443\r\n\r\n").encode())
            header=b""
            while b"\r\n\r\n" not in header and len(header)<8192:
                chunk=sock.recv(1024)
                if not chunk: break
                header+=chunk
            result["connect"]=header.split(b"\r\n",1)[0].decode("ascii","replace")[:100]
            if not result["connect"].startswith("HTTP/1.1 200") and not result["connect"].startswith("HTTP/1.0 200"):
                return jsonify(result)
            if header.partition(b"\r\n\r\n")[2]:
                result["tls"]="unexpected_proxy_data"
                return jsonify(result)
            with ssl.create_default_context().wrap_socket(sock,server_hostname=target) as tls:
                result["tls"]="verified"
                result["protocol"]=tls.version()
    except Exception as exc:
        result["error"]=type(exc).__name__
    return jsonify(result)

@app.get("/proxy-retry")
def proxy_retry():
    """Un intento por solicitud, sin exceder timeout del worker."""
    import time
    proxy=("45.181.123.185",999)
    target="bonowebptd.fonasa.cl"
    start=time.monotonic()
    result={"proxy":f"{proxy[0]}:{proxy[1]}","connect":"not_attempted","tls":"not_attempted"}
    try:
        with socket.create_connection(proxy,timeout=8) as sock:
            result["connect"]="tcp_connected"
            sock.settimeout(8)
            nl=chr(13)+chr(10)
            request_lines=["CONNECT "+target+":443 HTTP/1.1","Host: "+target+":443","",""]
            sock.sendall(nl.join(request_lines).encode("ascii"))
            header=b""
            terminator=bytes([13,10,13,10])
            while terminator not in header and len(header)<8192:
                chunk=sock.recv(1024)
                if not chunk: break
                header+=chunk
            firstline=header.split(bytes([13,10]),1)[0]
            result["connect"]=firstline.decode("ascii","replace")[:100]
            if result["connect"].startswith(("HTTP/1.0 200","HTTP/1.1 200")) and not header.partition(terminator)[2]:
                sock.settimeout(8)
                with ssl.create_default_context().wrap_socket(sock,server_hostname=target) as tls:
                    result["tls"]="verified"
                    result["version"]=tls.version()
    except Exception as exc:
        result["error"]=type(exc).__name__
    result["seconds"]=round(time.monotonic()-start,2)
    return jsonify(result)

@app.get("/chromium-login-check")
def chromium_login_check():
    """Chromium headless: solo pantalla de login, sin claves ni acciones."""
    from playwright.sync_api import sync_playwright
    import re, os
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
    result={"chromium":"not_started","portal":"not_loaded","usuario":False,"clave":False,"ingresar":False,"credentials_used":False}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"],timeout=9000)
            result["chromium"]="started"
            try:
                page=browser.new_page(ignore_https_errors=False)
                response=page.goto("https://bonowebptd.fonasa.cl/login.php",wait_until="commit",timeout=10000)
                result["portal"]="responded"
                result["http_status"]=response.status if response else None
                page.wait_for_load_state("domcontentloaded",timeout=5000)
                result["usuario"]=page.locator('input[type="text"]').count()>0
                result["clave"]=page.locator('input[type="password"]').count()>0
                result["ingresar"]=page.get_by_text(re.compile("Ingresar",re.I)).count()>0
            finally:
                browser.close()
    except Exception as exc:
        result["error_type"]=type(exc).__name__
    return jsonify(result)

@app.get("/chromium-proxy-check")
def chromium_proxy_check():
    """Prueba sin credenciales de Chromium a través de proxy experimental."""
    from playwright.sync_api import sync_playwright
    import os, re, time
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    result={"chromium":"not_started","portal":"not_loaded","usuario":False,"clave":False,"ingresar":False,"credentials_used":False,"proxy":"45.181.123.185:999"}
    started=time.monotonic()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(
                headless=True,
                proxy={"server":"http://45.181.123.185:999"},
                args=["--no-sandbox"],
                timeout=10000
            )
            result["chromium"]="started"
            try:
                page=browser.new_page(ignore_https_errors=False)
                response=page.goto("https://bonowebptd.fonasa.cl/login.php",wait_until="domcontentloaded",timeout=14000)
                result["portal"]="loaded" if page.url.startswith("https://bonowebptd.fonasa.cl/") else "redirected"
                result["http_status"]=response.status if response else None
                result["usuario"]=page.locator('input[type="text"]').count()>0
                result["clave"]=page.locator('input[type="password"]').count()>0
                result["ingresar"]=page.get_by_text(re.compile("Ingresar",re.I)).count()>0
            finally:
                browser.close()
    except Exception as exc:
        result["error_type"]=type(exc).__name__
    result["seconds"]=round(time.monotonic()-started,2)
    return jsonify(result)
