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
        match=re.search(r"net::ERR_[A-Z_]+", str(exc))
        result["network_error"]=match.group(0) if match else "unknown_browser_error"
    result["seconds"]=round(time.monotonic()-started,2)
    return jsonify(result)

@app.get("/socks5-check")
def socks5_check():
    """Prueba acotada SOCKS5 CONNECT hacia Fonasa; sin credenciales."""
    import time
    t0=time.monotonic()
    result={"proxy_type":"SOCKS5","proxy":"49.13.22.249:10805",
            "proxy_tcp":"not_connected","socks5":"not_tested","fonasa_tls":"not_tested",
            "credentials_used":False}
    try:
        with socket.create_connection(("49.13.22.249",10805), timeout=5) as sock:
            result["proxy_tcp"]="connected"
            sock.settimeout(15)
            sock.sendall(bytes([5,1,0]))
            hello=sock.recv(2)
            result["handshake_reply_hex"]=hello.hex()
            if hello != bytes([5,0]):
                result["socks5"]="handshake_rejected"
                return jsonify(result)
            result["socks5"]="handshake_accepted"
            name=b"bonowebptd.fonasa.cl"
            sock.sendall(bytes([5,1,0,3,len(name)])+name+bytes([1,187]))
            result["connect_request"]="sent"
            reply=sock.recv(4)
            result["connect_reply_hex"]=reply.hex()
            if len(reply)!=4 or reply[1]!=0:
                result["socks5"]="connect_rejected"
                result["reply_code"]=reply[1] if len(reply)>1 else None
                return jsonify(result)
            atyp=reply[3]
            address_length={1:4,4:16}.get(atyp)
            if atyp==3:
                count=sock.recv(1)
                address_length=count[0] if count else 0
            if address_length:
                needed=address_length+2
                while needed>0:
                    part=sock.recv(needed)
                    if not part: break
                    needed-=len(part)
            result["socks5"]="tunnel_connected"
            with ssl.create_default_context().wrap_socket(sock,server_hostname="bonowebptd.fonasa.cl") as tls:
                result["fonasa_tls"]="verified"
                result["tls_version"]=tls.version()
    except Exception as exc:
        result["error_type"]=type(exc).__name__
    result["elapsed_seconds"]=round(time.monotonic()-t0,2)
    return jsonify(result)

@app.get("/socks5-compare")
def socks5_compare():
    """Comparacion de tuneles sin introducir credenciales ni enviar HTTP."""
    import concurrent.futures
    import time
    proxy_host,proxy_port="49.13.22.249",10805
    def check(label,host,address_type):
        start=time.monotonic()
        out={"target":label,"proxy_tcp":"failed","handshake":"not_tested","tunnel":"not_tested","tls":"not_tested"}
        try:
            with socket.create_connection((proxy_host,proxy_port),timeout=4) as sock:
                out["proxy_tcp"]="connected"
                sock.settimeout(8)
                sock.sendall(bytes([5,1,0]))
                hello=sock.recv(2)
                if hello!=bytes([5,0]):
                    out["handshake"]="rejected"
                    return out
                out["handshake"]="accepted"
                if address_type=="ipv4":
                    addr=bytes([1])+socket.inet_aton(host)
                else:
                    encoded=host.encode("ascii")
                    addr=bytes([3])+bytes([len(encoded)])+encoded
                sock.sendall(bytes([5,1,0])+addr+bytes([1,187]))
                reply=sock.recv(4)
                if len(reply)!=4 or reply[1]!=0:
                    out["tunnel"]="rejected" if len(reply)>1 else "invalid_reply"
                    if len(reply)>1:out["reply_code"]=reply[1]
                    return out
                out["tunnel"]="connected"
                atyp=reply[3]
                if atyp==1:n=4
                elif atyp==4:n=16
                elif atyp==3:
                    v=sock.recv(1)
                    n=v[0] if v else 0
                else:n=0
                remaining=n+2
                while remaining>0:
                    buf=sock.recv(remaining)
                    if not buf:break
                    remaining-=len(buf)
                server_name="bonowebptd.fonasa.cl" if label.startswith("fonasa_portal") else "www.google.com"
                sock.settimeout(4)
                with ssl.create_default_context().wrap_socket(sock,server_hostname=server_name) as tls:
                    out["tls"]="verified"
                    out["tls_version"]=tls.version()
        except Exception as exc:
            out["error_type"]=type(exc).__name__
        finally:
            out["seconds"]=round(time.monotonic()-start,2)
        return out
    specs=[
      ("fonasa_portal_domain","bonowebptd.fonasa.cl","domain"),
      ("fonasa_portal_ipv4","190.215.211.22","ipv4"),
      ("google_control","www.google.com","domain")
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures=[executor.submit(check,*spec) for spec in specs]
        tests=[future.result() for future in futures]
    return jsonify({"proxy":"49.13.22.249:10805","credentials_used":False,"checks":tests})

@app.get("/chile-proxy-batch")
def chile_proxy_batch():
    """Probar proxies publicos sin credenciales, TLS verificado, destinos fijos."""
    import concurrent.futures, time
    candidates=[
      ("190.110.110.154",999),
      ("167.249.28.73",999),
      ("190.82.221.178",999),
      ("45.225.204.11",999),
      ("45.169.148.1",999),
      ("190.121.52.124",999),
      ("45.225.206.248",999),
      ("190.121.52.125",999)
    ]
    target="bonowebptd.fonasa.cl"
    def probe(candidate):
        host,port=candidate
        out={"proxy":host+":"+str(port),"proxy_tcp":"failed",
             "connect":"not_tested","tls":"not_tested",
             "control_connect":"not_tested","credentials_used":False}
        start=time.monotonic()
        try:
            with socket.create_connection((host,port),timeout=3.5) as sock:
                out["proxy_tcp"]="connected"
                sock.settimeout(5)
                sock.sendall(("CONNECT "+target+":443 HTTP/1.1\r\nHost: "+target+":443\r\nProxy-Connection: close\r\n\r\n").encode("ascii"))
                line=bytearray()
                while b"\r\n\r\n" not in line and len(line)<8192:
                    block=sock.recv(1024)
                    if not block: break
                    line.extend(block)
                header=line.split(b"\r\n",1)[0]
                status=header.decode("ascii","replace")[:110]
                out["connect"]=status
                if status.startswith("HTTP/1.1 200") or status.startswith("HTTP/1.0 200"):
                    sock.settimeout(6)
                    with ssl.create_default_context().wrap_socket(sock,server_hostname=target) as tls:
                        out["tls"]="verified"
                        out["tls_version"]=tls.version()
                        tls.settimeout(4)
                        tls.sendall(("GET /login.php HTTP/1.1\r\nHost: "+target+"\r\nConnection: close\r\n\r\n").encode("ascii"))
                        data=tls.recv(1024)
                        out["login_http"]=data.split(b"\r\n",1)[0].decode("ascii","replace")[:110]
        except Exception as exc:
            out["error_type"]=type(exc).__name__
        out["seconds"]=round(time.monotonic()-start,2)
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(probe,candidates))

    def control(candidate):
        host,port=candidate
        try:
            with socket.create_connection((host,port),timeout=3) as sock:
                sock.settimeout(4)
                sock.sendall(b"CONNECT www.google.com:443 HTTP/1.1\r\nHost: www.google.com:443\r\n\r\n")
                buf=bytearray()
                while b"\r\n\r\n" not in buf and len(buf)<4096:
                    piece=sock.recv(1024)
                    if not piece:break
                    buf.extend(piece)
                status=buf.split(b"\r\n",1)[0].decode("ascii","replace")
                if not status.startswith(("HTTP/1.1 200","HTTP/1.0 200")):
                    return status[:80] if status else "no_response"
                sock.settimeout(4)
                with ssl.create_default_context().wrap_socket(sock,server_hostname="www.google.com"):
                    return "tls_verified"
        except Exception as exc:
            return type(exc).__name__
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        control_results=list(pool.map(control,candidates))
    for result,control_status in zip(results,control_results):
        result["control_connect"]=control_status
    return jsonify({"target":target,"credentials_used":False,"candidates":results})

@app.get("/portal-alternativo")
def portal_alternativo():
    """Chromium directo al portalprestador.fonasa.cl sin autenticacion."""
    from playwright.sync_api import sync_playwright
    import time, os
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
    t=time.monotonic()
    result={"target":"https://portalprestador.fonasa.cl/","chromium":"not_started","portal":"not_loaded","run":False,"password":False,"ingresar":False,"credentials_used":False}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"],timeout=8500)
            result["chromium"]="started"
            try:
                page=browser.new_page(ignore_https_errors=False)
                response=page.goto(result["target"],wait_until="domcontentloaded",timeout=12000)
                result["portal"]="loaded"
                result["http_status"]=response.status if response else None
                result["final_host"]=page.url.split("/")[2] if "://" in page.url else ""
                result["run"]=page.locator('input[type="text"],input[name*="rut" i],input[name*="run" i]').count()>0
                result["password"]=page.locator('input[type="password"]').count()>0
                result["ingresar"]=page.get_by_text("Ingresar",exact=False).count()>0
            finally:
                browser.close()
    except Exception as exc:
        result["error_type"]=type(exc).__name__
        import re
        match=re.search(r"net::ERR_[A-Z_]+",str(exc))
        if match:result["network_error"]=match.group()
    result["seconds"]=round(time.monotonic()-t,2)
    return jsonify(result)

@app.get("/portal-domains")
def portal_domains():
    """Comparar DNS y TLS de dominios Fonasa desde Render; sin login."""
    import concurrent.futures, time
    domains=["portalprestador.fonasa.cl","bonowebptd.fonasa.cl","libre-eleccion.fonasa.gob.cl","www.google.com"]
    def check(host):
        out={"host":host,"dns":"not_resolved","tcp":"not_connected","tls":"not_tested"}
        try:
            ips=sorted({x[4][0] for x in socket.getaddrinfo(host,443,family=socket.AF_INET,type=socket.SOCK_STREAM)})
            out["dns"]=ips
            if not ips:return out
            with socket.create_connection((ips[0],443),timeout=6) as sock:
                out["tcp"]="connected"
                sock.settimeout(6)
                with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as tls:
                    out["tls"]="verified"
                    out["tls_version"]=tls.version()
        except Exception as exc:
            out["error_type"]=type(exc).__name__
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        checks=list(executor.map(check,domains))
    return jsonify({"checks":checks,"credentials_used":False})


@app.get("/network-path")
def network_path():
    """Fixed-target TCP comparison; no proxies, credentials or HTTP login."""
    import concurrent.futures
    import time
    from datetime import datetime, timezone
    def check(spec):
        host, port = spec
        start = time.monotonic()
        out = {"host": host, "port": port, "tcp": "not_connected",
               "tls": "not_tested"}
        try:
            ip = socket.getaddrinfo(host, port, family=socket.AF_INET,
                                    type=socket.SOCK_STREAM)[0][4][0]
            out["ip"] = ip
            with socket.create_connection((ip, port), timeout=4) as sock:
                out["tcp"] = "connected"
                if port == 443:
                    sock.settimeout(4)
                    with ssl.create_default_context().wrap_socket(
                            sock, server_hostname=host) as tls:
                        out["tls"] = "verified"
                        out["tls_version"] = tls.version()
        except (OSError, ssl.SSLError) as exc:
            out["error_type"] = type(exc).__name__
            out["errno"] = getattr(exc, "errno", None)
        out["seconds"] = round(time.monotonic() - start, 2)
        return out
    specs = [(host, port) for host in
             ("bonowebptd.fonasa.cl", "www.google.com") for port in (80, 443)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        checks = list(executor.map(check, specs))
    result = {"utc": datetime.now(timezone.utc).isoformat(),
              "credentials_used": False, "proxy_used": False,
              "checks": checks,
              "note": "Port 80 is diagnostic only, never a login fallback. "
                      "Timeout alone does not prove geoblocking."}
    app.logger.warning("FONASA_NETWORK_PATH %s", result)
    return jsonify(result)

# Decodo trial: protected, bounded diagnostics; NEVER log proxy or Fonasa credentials.
_decodo_check_count = 0

@app.get("/decodo-diagnostico")
def decodo_diagnostico():
    import os, hmac, json, re, time
    from flask import request
    from playwright.sync_api import sync_playwright
    expected_token = os.environ.get("DECODO_TEST_TOKEN", "")
    provided_token = request.args.get("key", "")
    if not expected_token or not hmac.compare_digest(provided_token, expected_token):
        return jsonify({"error": "unauthorized"}), 403

    required = ("DECODO_PROXY_HOST", "DECODO_PROXY_PORT",
                "DECODO_PROXY_USER", "DECODO_PROXY_PASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        return jsonify({"status":"needs_configuration", "missing":missing,
                        "fonasa_credentials_used":False})

    global _decodo_check_count
    if _decodo_check_count >= 3:
        return jsonify({"status":"limit_reached",
                        "message":"Three tests max per running service instance"}), 429
    _decodo_check_count += 1

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    host = os.environ["DECODO_PROXY_HOST"].strip()
    port = os.environ["DECODO_PROXY_PORT"].strip()
    if not port.isdecimal() or not (1 <= int(port) <= 65535):
        return jsonify({"status":"invalid_proxy_port"}), 400
    server = host if "://" in host else "http://" + host
    server = server.rstrip("/") + ":" + port
    result = {"status":"started","chromium":"not_started",
              "proxy_control":"not_tested","proxy_country":"unknown",
              "fonasa":"not_tested","fonasa_credentials_used":False}
    started=time.monotonic()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,args=["--no-sandbox"],timeout=8000,
                proxy={"server":server,
                       "username":os.environ["DECODO_PROXY_USER"],
                       "password":os.environ["DECODO_PROXY_PASSWORD"]})
            result["chromium"]="started"
            try:
                context = browser.new_context(ignore_https_errors=False)
                context.route("**/*", lambda r: r.abort()
                    if r.request.resource_type in ("image","font","media")
                    else r.continue_())
                ip_page = context.new_page()
                try:
                    ip_resp=ip_page.goto("https://ipinfo.io/json",
                                         wait_until="domcontentloaded",timeout=6500)
                    result["proxy_control"]="loaded"
                    result["proxy_http_status"]=ip_resp.status if ip_resp else None
                    try:
                        obj=json.loads(ip_page.locator("body").inner_text(timeout=2000))
                        result["proxy_country"]=obj.get("country","unknown")
                    except (ValueError,Exception):
                        pass
                except Exception as err:
                    result["proxy_control_error"]=type(err).__name__
                    m=re.search(r"net::ERR_[A-Z_]+",str(err))
                    if m: result["proxy_network_error"]=m.group(0)
                finally:
                    ip_page.close()
                if result["proxy_country"]=="CL":
                    page=context.new_page()
                    try:
                        response=page.goto("https://bonowebptd.fonasa.cl/",
                                            wait_until="domcontentloaded",timeout=12000)
                        result["fonasa"]="http_response"
                        result["fonasa_http_status"]=response.status if response else None
                        result["username_field"]=page.locator(
                            'input[type="text"],input[name*="rut" i],input[name*="run" i]').count()>0
                        result["password_field"]=page.locator('input[type="password"]').count()>0
                        result["login_button"]=page.get_by_text(
                            re.compile("Ingresar|Iniciar sesi[oó]n|Entrar",re.I)).count()>0
                        result["fonasa"]="form_detected" if (
                            result["username_field"] and result["password_field"]
                        ) else "http_response_without_recognized_form"
                    except Exception as err:
                        result["fonasa"]="navigation_failed"
                        result["fonasa_error"]=type(err).__name__
                        m=re.search(r"net::ERR_[A-Z_]+",str(err))
                        if m: result["fonasa_network_error"]=m.group(0)
                    finally:
                        page.close()
                else:
                    result["fonasa"]="skipped_proxy_not_confirmed_chile"
                context.close()
            finally:
                browser.close()
    except Exception as err:
        result["error_type"]=type(err).__name__
    result["seconds"]=round(time.monotonic()-started,2)
    return jsonify(result)

def _decodo_diagnostic_startup_once():
    """One bounded diagnostic on explicit operator opt-in, logs status only."""
    import json, os, time
    time.sleep(2)
    try:
        key=os.environ.get("DECODO_TEST_TOKEN","")
        if not key:
            app.logger.warning("DECODO_STARTUP_RESULT %s",
                               json.dumps({"status":"missing_test_token"}))
            return
        with app.test_client() as client:
            response=client.get("/decodo-diagnostico",query_string={"key":key})
            payload=response.get_json(silent=True)
            if not isinstance(payload,dict):
                payload={"status":"no_json","http_status":response.status_code}
            payload["diagnostic_http_status"]=response.status_code
            app.logger.warning("DECODO_STARTUP_RESULT %s",
                               json.dumps(payload,ensure_ascii=True))
    except Exception as exc:
        app.logger.warning("DECODO_STARTUP_RESULT %s",
                           json.dumps({"error_type":type(exc).__name__}))

if __import__("os").environ.get("DECODO_DIAGNOSTIC_ON_START")=="yes":
    import threading
    threading.Thread(target=_decodo_diagnostic_startup_once,daemon=True).start()

# One-shot Fonasa login inspection: provider credentials never appear in responses/logs.
_fonasa_login_attempted = False

@app.post("/fonasa-login-inspect")
def fonasa_login_inspect():
    import os, hmac, re, time, json
    from urllib.parse import urlparse
    from flask import request
    from playwright.sync_api import sync_playwright
    global _fonasa_login_attempted
    token=os.environ.get("DECODO_TEST_TOKEN","")
    if not token or not hmac.compare_digest(request.headers.get("X-Test-Token",""),token):
        return jsonify({"status":"unauthorized"}),403
    required=["DECODO_PROXY_HOST","DECODO_PROXY_PORT","DECODO_PROXY_USER","DECODO_PROXY_PASSWORD",
              "FONASA_MLE_USER","FONASA_MLE_PASSWORD"]
    missing=[v for v in required if not os.environ.get(v)]
    if missing:
        return jsonify({"status":"missing_environment_variables","missing":missing}),200
    if _fonasa_login_attempted:
        return jsonify({"status":"already_attempted_in_this_process"}),429
    _fonasa_login_attempted=True
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
    start=time.monotonic()
    result={"status":"started","proxy_chile":"not_checked","chromium":"not_started",
            "login_page":"not_loaded","login_submit":"not_attempted",
            "authenticated_menu":"not_observed",
            "activation_form":"not_observed",
            "no_bono_transaction":True,"credentials_printed":False}
    host="bonowebptd.fonasa.cl"
    try:
        with sync_playwright() as p:
            proxy_server="http://"+os.environ["DECODO_PROXY_HOST"].strip()+":"+os.environ["DECODO_PROXY_PORT"].strip()
            browser=p.chromium.launch(
                headless=True,args=["--no-sandbox"],timeout=8000,
                proxy={"server":proxy_server,"username":os.environ["DECODO_PROXY_USER"],
                       "password":os.environ["DECODO_PROXY_PASSWORD"]})
            result["chromium"]="started"
            try:
                context=browser.new_context(ignore_https_errors=False)
                context.set_default_timeout(5000)
                context.route("**/*",lambda route: route.abort()
                    if route.request.resource_type in ("image","media","font")
                    else route.continue_())
                check=context.new_page()
                try:
                    response=check.goto("https://ipinfo.io/json",wait_until="domcontentloaded",timeout=6500)
                    if response and response.status==200:
                        geo=json.loads(check.locator("body").inner_text(timeout=1500))
                        result["proxy_chile"]="confirmed" if geo.get("country")=="CL" else "not_chile"
                except Exception as exc:
                    result["proxy_error"]=type(exc).__name__
                finally:
                    check.close()
                if result["proxy_chile"]!="confirmed":
                    result["status"]="stopped_proxy_unconfirmed"
                    return jsonify(result)
                page=context.new_page()
                try:
                    response=page.goto("https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80",
                         wait_until="domcontentloaded",timeout=12000)
                    result["login_http_status"]=response.status if response else None
                    parsed=urlparse(page.url)
                    if parsed.scheme!="https" or parsed.hostname!=host:
                        result["status"]="stopped_unexpected_login_origin"
                        return jsonify(result)
                    result["login_page"]="loaded"
                    username=page.locator("#CodUsuario")
                    password=page.locator("#Password")
                    login=page.locator('button[onclick="validafrmLogin()"]')
                    result["field_counts"]={"usuario":username.count(),"clave":password.count(),"ingresar":login.count()}
                    if username.count()!=1 or password.count()!=1 or login.count()!=1:
                        result["status"]="stopped_unrecognized_login_form"
                        result["form_inputs"]=[{"type":item.get_attribute("type"),"name":item.get_attribute("name"),
                            "id":item.get_attribute("id")} for item in page.locator("input").all()[:12]]
                        return jsonify(result)
                    # Record only status codes and resource type for same-origin XHR/fetch.
                    network_events=[]
                    def record_login_response(response):
                        try:
                            kind=response.request.resource_type
                            if kind in ("xhr","fetch","document") and urlparse(response.url).hostname==host:
                                network_events.append({"kind":kind,"status":response.status})
                        except Exception:
                            pass
                    page.on("response",record_login_response)
                    result["login_function"] = page.evaluate("""() => {
                       const src = typeof validafrmLogin === 'function' ? String(validafrmLogin) : '';
                       return {
                         function_found: src.length > 0,
                         mentions_grecaptcha: /grecaptcha|recaptcha/i.test(src),
                         mentions_ajax: /ajax|XMLHttpRequest|fetch\\(/i.test(src),
                         mentions_loading: /loading|spinner|wait|espera|cargando/i.test(src)
                       };
                    }""")
                    username.fill(os.environ["FONASA_MLE_USER"])
                    password.fill(os.environ["FONASA_MLE_PASSWORD"])
                    if urlparse(page.url).hostname!=host:
                        result["status"]="stopped_origin_changed_before_submit"
                        return jsonify(result)
                    login.click(timeout=6000)
                    result["login_submit"]="clicked"
                    try:page.wait_for_load_state("domcontentloaded",timeout=8000)
                    except Exception:pass
                    page.wait_for_timeout(5000)
                    result["server_response_statuses"]=network_events[:15]
                    challenge=page.locator('iframe[src*="recaptcha"], .g-recaptcha')
                    result["captcha_elements_present"]=challenge.count()
                    result["captcha_actually_visible"]=any(el.is_visible() for el in challenge.all())
                    result["loading_indicator_visible"]=page.locator(
                        ".spinner:visible, .loading:visible, [class*='loading']:visible, [id*='loading']:visible").count()>0
                    try:
                        raw_text=page.locator("body").inner_text(timeout=2000).lower()
                        result["possible_auth_error"]=any(word in raw_text for word in (
                            "contraseña incorrecta","clave incorrecta","usuario incorrecto",
                            "datos incorrectos","usuario o contraseña","credenciales incorrectas"))
                    except Exception:
                        result["possible_auth_error"]=False
                    result["login_form_still_visible"]=page.locator('input[type="password"]').count()>0
                    result["after_login_host"]=urlparse(page.url).hostname
                    if urlparse(page.url).hostname!=host:
                        result["status"]="stopped_post_login_redirect_other_host"
                        return jsonify(result)
                    menu=page.get_by_text(re.compile(r"Activaci[oó]n y desactivar Cuenta M[eé]dica",re.I))
                    result["activation_menu_matches"]=menu.count()
                    if menu.count()>0:
                        result["authenticated_menu"]="observed"
                        try:
                            menu.first.click(timeout=5000)
                            page.wait_for_timeout(600)
                            submenu=page.get_by_text(re.compile(r"Activaci[oó]n Cuenta M[eé]dica",re.I))
                            result["activation_submenu_matches"]=submenu.count()
                            if submenu.count()>0:
                                submenu.first.click(timeout=5000)
                                page.wait_for_timeout(2300)
                                rut=page.get_by_text(re.compile(r"Rut Beneficiario",re.I))
                                folio=page.get_by_text(re.compile(r"Folio Bono",re.I))
                                result["activation_form"]="observed" if rut.count()>0 and folio.count()>0 else "not_observed"
                                # Inspect field identifiers even if labels have not rendered.
                                result["form_controls"] = page.locator("input, button").evaluate_all("""els => els.filter(el => el.getClientRects().length>0).map(el => ({
                                    tag:el.tagName.toLowerCase(),
                                    id:el.id||"",name:el.name||"",type:el.type||"",
                                    button_text:el.tagName.toLowerCase()==="button"?(el.textContent||"").trim().slice(0,40):"",
                                    button_value:(el.type==="button"||el.type==="submit")?(el.value||"").slice(0,40):"",
                                    onclick:(el.getAttribute("onclick")||"").slice(0,90)
                                })).filter(e => e.id || e.name || e.button_text || e.button_value).slice(-32)""")
                                result["buscar_button_count"]=page.get_by_role(
                                    "button",name=re.compile(r"^Buscar$",re.I)).count()
                                result["buscar_text_count"]=page.get_by_text(
                                    re.compile(r"^Buscar$",re.I)).count()
                                # Controls only: never capture input values, records, or personal data.
                                if result["activation_form"]=="observed":
                                    result["activation_fields"] = {
                                        "rut_label_matches": rut.count(),
                                        "folio_label_matches": folio.count()
                                    }
                                    result["form_controls"] = page.locator("input, button").evaluate_all("""els => els.slice(0,90)
                                        .filter(el => el.getClientRects().length > 0)
                                        .map(el => ({
                                           tag:el.tagName.toLowerCase(),
                                           id:el.id||"", name:el.name||"",
                                           type:el.type||"",
                                           button_text:el.tagName.toLowerCase()==="button"
                                             ? (el.textContent||"").trim().slice(0,45) : "",
                                           button_value:(el.type==="button" || el.type==="submit")
                                             ? (el.value||"").slice(0,45) : "",
                                           onclick:(el.getAttribute("onclick")||"").slice(0,100)
                                        }))
                                        .filter(el => el.id || el.name || el.button_text || el.button_value)
                                        .slice(-35)""")
                                    result["buscar_button_count"]=page.get_by_role(
                                        "button",name=re.compile(r"^Buscar$",re.I)).count()
                                    result["buscar_text_count"]=page.get_by_text(
                                        re.compile(r"^Buscar$",re.I)).count()
                            else:result["activation_form"]="submenu_not_found"
                        except Exception as exc:
                            result["menu_inspection_error"]=type(exc).__name__
                        result["status"]="menu_observed_no_patient_actions"
                    else:
                        result["status"]="login_clicked_menu_not_confirmed"
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["status"]="test_failed"
        result["error_type"]=type(exc).__name__
        code=re.search(r"net::ERR_[A-Z_]+",str(exc))
        if code:result["network_error"]=code.group(0)
    result["seconds"]=round(time.monotonic()-start,2)
    return jsonify(result)


def _fonasa_login_startup_probe():
    """Operator-activated single test; emits only redacted result flags."""
    import os, json, time
    time.sleep(2)
    key=os.environ.get("DECODO_TEST_TOKEN","")
    if not key:
        app.logger.warning("FONASA_LOGIN_RESULT %s",json.dumps({"status":"missing_test_token"}))
        return
    try:
        with app.test_client() as client:
            response=client.post("/fonasa-login-inspect",headers={"X-Test-Token":key})
            payload=response.get_json(silent=True) or {"status":"non_json_response"}
            app.logger.warning("FONASA_LOGIN_RESULT %s",json.dumps(payload))
    except Exception as exc:
        app.logger.warning("FONASA_LOGIN_RESULT %s",json.dumps({"error_type":type(exc).__name__}))

if __import__("os").environ.get("FONASA_LOGIN_ON_START")=="yes":
    import threading
    threading.Thread(target=_fonasa_login_startup_probe,daemon=True).start()


# Single operator-authorized real voucher lookup; the voucher is NEVER activated.
_fonasa_lookup_attempted = False

@app.post("/fonasa-lookup-once")
def fonasa_lookup_once():
    import os, hmac, json, re, time, sys
    from urllib.parse import urlparse
    from flask import request
    from playwright.sync_api import sync_playwright
    global _fonasa_lookup_attempted
    secret=os.environ.get("DECODO_TEST_TOKEN","")
    if not secret or not hmac.compare_digest(request.headers.get("X-Test-Token",""),secret):
        return jsonify({"status":"unauthorized"}),403
    keys=("DECODO_PROXY_HOST","DECODO_PROXY_PORT","DECODO_PROXY_USER","DECODO_PROXY_PASSWORD",
          "FONASA_MLE_USER","FONASA_MLE_PASSWORD","FONASA_TEST_RUT","FONASA_TEST_FOLIO")
    missing=[key for key in keys if not os.environ.get(key)]
    if missing:
        return jsonify({"status":"missing_configuration","keys":missing}),200
    if _fonasa_lookup_attempted:
        return jsonify({"status":"already_executed"}),429
    _fonasa_lookup_attempted=True
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
    sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
    from fonasa_mle_busqueda import buscar_cuenta_medica, validar_rut
    rut=validar_rut(os.environ["FONASA_TEST_RUT"])
    folio=os.environ["FONASA_TEST_FOLIO"]
    if not re.fullmatch(r"\d{5,30}",folio):
        return jsonify({"status":"invalid_folio"}),400
    host="bonowebptd.fonasa.cl"
    result={"status":"started","proxy":"not_checked","chromium":"not_started",
            "login":"not_attempted","activation_form":"not_observed",
            "search_clicked":False,"voucher_activated":False,
            "records_logged":False,"screenshot_logged":False}
    t0=time.monotonic()
    try:
        proxy_server="http://"+os.environ["DECODO_PROXY_HOST"].strip()+":"+os.environ["DECODO_PROXY_PORT"].strip()
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"],timeout=9000,
                proxy={"server":proxy_server,"username":os.environ["DECODO_PROXY_USER"],
                       "password":os.environ["DECODO_PROXY_PASSWORD"]})
            result["chromium"]="started"
            try:
                context=browser.new_context(ignore_https_errors=False)
                context.set_default_timeout(5000)
                context.route("**/*",lambda route: route.abort()
                    if route.request.resource_type in ("image","font","media")
                    else route.continue_())
                geo_page=context.new_page()
                try:
                    response=geo_page.goto("https://ipinfo.io/json",wait_until="domcontentloaded",timeout=7000)
                    if response and response.status==200:
                        geo=json.loads(geo_page.locator("body").inner_text(timeout=1200))
                        result["proxy"]="chile" if geo.get("country")=="CL" else "other_location"
                except Exception as exc:
                    result["proxy"]="geo_check_failed"
                    result["geo_error"]=type(exc).__name__
                finally:
                    geo_page.close()
                if result["proxy"]!="chile":
                    result["status"]="stopped_proxy_not_chile"
                    return jsonify(result)
                page=context.new_page()
                events=[]
                def on_response(response):
                    try:
                        if (urlparse(response.url).hostname==host and
                            response.request.resource_type in ("document","xhr","fetch")):
                            events.append({"type":response.request.resource_type,"status":response.status})
                    except Exception:
                        pass
                page.on("response",on_response)
                try:
                    resp=page.goto("https://bonowebptd.fonasa.cl/login.php?v=18-06-2019_1.80",
                                   wait_until="domcontentloaded",timeout=13000)
                    if urlparse(page.url).hostname!=host:
                        result["status"]="unexpected_origin"
                        return jsonify(result)
                    user_el=page.locator("#CodUsuario")
                    pass_el=page.locator("#Password")
                    login_el=page.locator('button[onclick="validafrmLogin()"]')
                    if any(el.count()!=1 for el in (user_el,pass_el,login_el)):
                        result["status"]="login_controls_missing"
                        return jsonify(result)
                    user_el.fill(os.environ["FONASA_MLE_USER"])
                    pass_el.fill(os.environ["FONASA_MLE_PASSWORD"])
                    login_el.click(timeout=6000)
                    result["login"]="clicked"
                    try:page.wait_for_load_state("domcontentloaded",timeout=8500)
                    except Exception:pass
                    page.wait_for_timeout(3800)
                    if urlparse(page.url).hostname!=host:
                        result["status"]="login_origin_unexpected"
                        return jsonify(result)
                    menu=page.get_by_text(re.compile(r"Activaci[oó]n y desactivar Cuenta M[eé]dica",re.I))
                    try:
                        menu.first.wait_for(state="visible",timeout=12000)
                    except Exception:
                        result["status"]="menu_not_visible_after_wait"
                        result["login_form_visible"]=page.locator("#Password:visible").count()>0
                        return jsonify(result)
                    result["login"]="authenticated"
                    menu.first.click(timeout=6000)
                    page.wait_for_timeout(700)
                    submenu=page.get_by_text(re.compile(r"Activaci[oó]n Cuenta M[eé]dica",re.I))
                    if submenu.count()==0:
                        result["status"]="activation_submenu_missing"
                        return jsonify(result)
                    submenu.first.click(timeout=6000)
                    try:
                        page.locator("#RutBeneficiario").wait_for(state="visible",timeout=10000)
                        page.locator("#FolioBono").wait_for(state="visible",timeout=4000)
                        page.locator("#btnBuscar").wait_for(state="visible",timeout=4000)
                    except Exception:
                        result["status"]="activation_form_missing"
                        return jsonify(result)
                    result["activation_form"]="observed"
                    events.clear()
                    buscar_cuenta_medica(page,rut_beneficiario=rut,folio=folio)
                    result["search_clicked"]=True
                    result["status"]="search_submitted"
                    page.wait_for_timeout(5000)
                    result["response_statuses"]=events[:18]
                    result["origin_after_search_ok"]=urlparse(page.url).hostname==host
                    # Read result text only in-memory; publish generic classification flags,
                    # never the full text, patient's identity, or raw HTML into logs.
                    text=(page.locator("body").inner_text(timeout=5000) or "").casefold()
                    markers={
                        "no_records":r"no se encontr[oó]|sin resultados|no existe el bono|bono no encontrado|no se ha encontrado",
                        "previously_activated":r"ya (?:se encuentra|est[aá]) activad|bono activado",
                        "not_activated":r"no (?:se encuentra|est[aá]) activad|sin activar",
                        "invalid_voucher":r"folio inv[aá]lido|bono inv[aá]lido|rut inv[aá]lido",
                        "general_error":r"ocurri[oó] un error|error de consulta|intente nuevamente",
                        "result_table":r"detalle (?:del )?bono|datos (?:del )?bono|resultado de (?:la )?b[uú]squeda"
                    }
                    result["status_clues"]={key:bool(re.search(pattern,text))
                                           for key,pattern in markers.items()}
                    result["visible_buttons"]=page.locator("button:visible").evaluate_all(
                        """els => els.map(e => (e.textContent||'').trim())
                           .filter(x => ['Buscar','Limpiar','Activar','Desactivar','Volver','Aceptar',
                                          'Salir','Cerrar','Confirmar'].includes(x)).slice(0,18)""")
                    result["visible_table_count"]=page.locator("table:visible").count()
                    result["visible_table_rows"]=page.locator("table:visible tr:visible").count()
                    result["visible_modal_count"]=page.locator(".modal:visible,[role='dialog']:visible").count()
                    result["login_form_visible"]=page.locator("#Password:visible").count()>0
                    result["search_form_visible"]=page.locator("#RutBeneficiario:visible").count()>0
                    # No screenshots saved: after a live query they may expose medical data.
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["status"]="failed"
        result["error_type"]=type(exc).__name__
        code=re.search(r"net::ERR_[A-Z_]+",str(exc))
        if code: result["network_error"]=code.group(0)
    result["elapsed_seconds"]=round(time.monotonic()-t0,2)
    return jsonify(result)


def _fonasa_lookup_startup_once():
    """Run once only when enabled, log status flags, never identifiers."""
    import os, json, time
    time.sleep(2)
    secret=os.environ.get("DECODO_TEST_TOKEN","")
    try:
        with app.test_client() as c:
            response=c.post("/fonasa-lookup-once",headers={"X-Test-Token":secret})
            payload=response.get_json(silent=True) or {"status":"no_json"}
            app.logger.warning("FONASA_LOOKUP_SAFE_RESULT %s",json.dumps(payload,ensure_ascii=True))
    except Exception as exc:
        app.logger.warning("FONASA_LOOKUP_SAFE_RESULT %s",
                           json.dumps({"status":"failed","error_type":type(exc).__name__}))

if __import__("os").environ.get("FONASA_LOOKUP_ON_START")=="yes":
    import threading
    threading.Thread(target=_fonasa_lookup_startup_once,daemon=True).start()

# Internal PSC administration bridge. Credentials and patient data are never returned.
from fonasa_live_bridge import bp as fonasa_live_bridge_bp
app.register_blueprint(fonasa_live_bridge_bp)


@app.post("/sii-eboleta-reachability")
def sii_eboleta_reachability():
    """Authenticated, read-only Chromium+Decodo SII connectivity check.

    Never authenticates, supplies credentials, clicks or submits a form.
    Never returns page HTML, cookies, tokens or proxy secrets.
    """
    import hmac
    import os
    import re
    import time
    from urllib.parse import urlparse
    from flask import request, jsonify
    from playwright.sync_api import sync_playwright
    secret=os.environ.get("DECODO_TEST_TOKEN","")
    if not secret or not hmac.compare_digest(
            request.headers.get("X-Test-Token",""),secret):
        return jsonify(status="unauthorized"),403
    keys=("DECODO_PROXY_HOST","DECODO_PROXY_PORT",
          "DECODO_PROXY_USER","DECODO_PROXY_PASSWORD")
    missing=[k for k in keys if not os.environ.get(k)]
    if missing:
        return jsonify(status="configuration_missing",missing=missing),503
    result={"status":"started","browser":"not_started",
            "sii_http_status":None,"sii_origin_ok":False,
            "login_form_detected":False,"authenticated":False,
            "credentials_used":False,"submitted":False,"emitted":False}
    started=time.monotonic()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"]="0"
    proxy_server="http://"+os.environ["DECODO_PROXY_HOST"].strip()+":"+os.environ["DECODO_PROXY_PORT"].strip()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(
                headless=True,args=["--no-sandbox"],timeout=12000,
                proxy={"server":proxy_server,"username":os.environ["DECODO_PROXY_USER"],
                       "password":os.environ["DECODO_PROXY_PASSWORD"]})
            result["browser"]="started"
            try:
                context=browser.new_context(ignore_https_errors=False)
                context.route("**/*",lambda route:route.abort()
                    if route.request.resource_type in ("image","font","media")
                    else route.continue_())
                page=context.new_page()
                try:
                    response=page.goto("https://eboleta.sii.cl/emitir/",
                                       wait_until="domcontentloaded",timeout=18000)
                    origin=urlparse(page.url)
                    result["sii_http_status"]=response.status if response else None
                    result["sii_origin_ok"]=origin.scheme=="https" and origin.hostname=="eboleta.sii.cl"
                    result["redirect_host"]=origin.hostname if origin.hostname in (
                        "eboleta.sii.cl","www.sii.cl","zeusr.sii.cl","hercules.sii.cl") else "other"
                    result["login_form_detected"]=page.locator('input[type="password"]').count()>0
                    result["status"]="page_loaded" if result["sii_origin_ok"] else "redirected_or_blocked"
                finally:
                    page.close()
                    context.close()
            finally:
                browser.close()
    except Exception as exc:
        result["status"]="connection_error"
        result["error_type"]=type(exc).__name__
        code=re.search(r"net::ERR_[A-Z_]+",str(exc))
        if code:result["network_error"]=code.group(0)
    result["elapsed_seconds"]=round(time.monotonic()-started,2)
    return jsonify(result)


def _sii_reachability_startup_once():
    """One read-only diagnostic on startup; report only sanitized state."""
    import json
    import logging
    import threading
    import time
    import os
    time.sleep(3)
    token=os.environ.get("DECODO_TEST_TOKEN","")
    if not token:
        logging.getLogger("sii_probe").warning('SII_REACHABILITY %s',
             json.dumps({"status":"token_not_configured"}))
        return
    try:
        with app.test_client() as client:
            response=client.post("/sii-eboleta-reachability",
                                 headers={"X-Test-Token":token})
            body=response.get_json(silent=True) or {}
            allowed=("status","browser","sii_http_status","sii_origin_ok",
                     "redirect_host","login_form_detected",
                     "authenticated","credentials_used","submitted",
                     "emitted","error_type","network_error")
            report={k:body[k] for k in allowed if k in body}
            logging.getLogger("sii_probe").warning("SII_REACHABILITY %s",json.dumps(report))
    except Exception as exc:
        logging.getLogger("sii_probe").warning("SII_REACHABILITY %s",
             json.dumps({"status":"internal_probe_failed","error_type":type(exc).__name__}))


import threading as _sii_probe_threading
_sii_probe_threading.Thread(target=_sii_reachability_startup_once,daemon=True).start()


def _sii_direct_comparison_once():
    """One startup diagnostic: compare direct HTTPS access to public SII pages.

    Uses no proxy and no user credentials. No HTML, cookies or secrets logged.
    """
    import json
    import logging
    import time
    from urllib.parse import urlparse
    from playwright.sync_api import sync_playwright
    time.sleep(5)
    result={"status":"started","credentials_used":False,"submitted":False,"emitted":False}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=["--no-sandbox"],timeout=12000)
            try:
                context=browser.new_context(ignore_https_errors=False)
                for name,url,host in (
                    ("sii_home","https://www.sii.cl/","www.sii.cl"),
                    ("eboleta","https://eboleta.sii.cl/emitir/","eboleta.sii.cl")):
                    page=context.new_page()
                    try:
                        response=page.goto(url,wait_until="commit",timeout=12000)
                        result[name]={"http":response.status if response else None,
                                      "origin_matches":urlparse(page.url).hostname==host,
                                      "status":"connected"}
                    except Exception as exc:
                        import re
                        match=re.search(r"net::ERR_[A-Z_]+",str(exc))
                        result[name]={"status":"failed","error_type":type(exc).__name__,
                                      "network_error":match.group(0) if match else "none"}
                    finally:page.close()
                context.close()
            finally:browser.close()
        result["status"]="completed"
    except Exception as exc:
        result["status"]="browser_error"
        result["error_type"]=type(exc).__name__
    logging.getLogger("sii_probe").warning("SII_DIRECT_COMPARISON %s",json.dumps(result))


import threading as _sii_compare_threading
_sii_compare_threading.Thread(target=_sii_direct_comparison_once,daemon=True).start()
