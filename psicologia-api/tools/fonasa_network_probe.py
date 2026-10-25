"""Diagnóstico aislado de conectividad a portal Fonasa; sin credenciales."""
import json, socket, ssl
from flask import Flask, jsonify
app=Flask(__name__)
HOST="bonowebptd.fonasa.cl"
@app.get("/")
def diagnostic():
    result={"target":HOST,"port":443,"dns":[],"tcp":"not_attempted","tls":"not_attempted"}
    try:
        addresses=sorted({x[4][0] for x in socket.getaddrinfo(HOST,443,type=socket.SOCK_STREAM)})
        result["dns"]=addresses
    except Exception as exc:
        result["dns_error"]=type(exc).__name__
        return jsonify(result)
    for ip in addresses:
        try:
            with socket.create_connection((ip,443),timeout=5) as sock:
                result["tcp"]="connected"
                try:
                    with ssl.create_default_context().wrap_socket(sock,server_hostname=HOST) as tls:
                        result["tls"]="verified"
                        result["tls_protocol"]=tls.version()
                except Exception as exc:
                    result["tls"]=type(exc).__name__
                break
        except Exception as exc:
            result["tcp"]=type(exc).__name__
    result["note"]="No browser, credentials, login, patients or POST requests used"
    return jsonify(result)
