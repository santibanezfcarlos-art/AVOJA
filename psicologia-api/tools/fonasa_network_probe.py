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
