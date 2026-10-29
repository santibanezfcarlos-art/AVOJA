#!/bin/sh
set -eu
mkdir -p /run
VNC_PASSWORD="$(python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(8)))')"
printf '%s' "$VNC_PASSWORD" | x11vnc -storepasswd stdin /run/vnc-pass >/dev/null 2>&1
chmod 600 /run/vnc-pass
export SII_VISUAL_VNC_PASSWORD="$VNC_PASSWORD"
unset VNC_PASSWORD
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
