"""Private, read-only Decodo credential bridge for the PSC SII visual session.
Does not alter Fonasa endpoints or flows. Never logs or persists credentials.
"""
import hmac
import os
from flask import Blueprint, jsonify, request

bp = Blueprint("sii_visual_proxy_bridge", __name__)


@bp.post("/internal/sii-visual-proxy")
def sii_visual_proxy_config():
    expected = os.environ.get("SII_VISUAL_BRIDGE_TOKEN", "")
    supplied = request.headers.get("X-SII-Visual-Bridge", "")
    if not expected or not hmac.compare_digest(supplied, expected):
        return jsonify(status="unauthorized"), 403

    names = ("DECODO_PROXY_HOST", "DECODO_PROXY_PORT",
             "DECODO_PROXY_USER", "DECODO_PROXY_PASSWORD")
    config = {name: os.environ.get(name, "").strip() for name in names}
    if not all(config.values()):
        return jsonify(status="proxy_configuration_incomplete"), 503
    if not config["DECODO_PROXY_PORT"].isdecimal():
        return jsonify(status="proxy_configuration_invalid"), 503
    if not 1 <= int(config["DECODO_PROXY_PORT"]) <= 65535:
        return jsonify(status="proxy_configuration_invalid"), 503
    return jsonify(status="ready", proxy=config), 200
