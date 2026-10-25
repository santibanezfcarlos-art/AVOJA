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
