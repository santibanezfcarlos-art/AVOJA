#!/bin/sh
set -eu
# Render discovers the public web service by probing a listening HTTP port.
# Materialize and validate nginx's listener before starting the process group.
# Keep the SII Chromium worker and VNC proxy behind nginx.
export PORT="${PORT:-10000}"
case "$PORT" in
  *[!0-9]*|'') echo "PSC_SII_INVALID_PORT" >&2; exit 1 ;;
esac
envsubst '${PORT}' < /srv/nginx.conf.template > /etc/nginx/conf.d/default.conf
nginx -t
printf 'PSC_SII_NGINX_CONFIGURED port=%s\n' "$PORT"
mkdir -p /run
VNC_PASSWORD="$(python -c 'import secrets,string; print("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(8)))')"
x11vnc -storepasswd "$VNC_PASSWORD" /run/vnc-pass >/dev/null 2>&1
chmod 600 /run/vnc-pass
export SII_VISUAL_VNC_PASSWORD="$VNC_PASSWORD"
unset VNC_PASSWORD
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
