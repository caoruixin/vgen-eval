#!/usr/bin/env bash
# Idempotent host setup for the vgen·eval hosted demo on the llm-pro ECS. Run as root from an unpacked deploy/
# directory (deploy/deploy.sh copies it to /tmp/vgen-deploy and runs it).
#   - vgenapp user, /opt/vgen (code), /var/lib/vgen (data), /etc/vgen/vgen.env (from the template, if missing)
#   - the systemd unit (enabled; deploy.sh starts it)
#   - once DNS for $DOMAIN resolves: its own Let's Encrypt certificate (webroot) and /etc/nginx/conf.d/vgen.conf
# Every nginx change is checked with `nginx -t` and removed again on failure, after a backup of /etc/nginx.
# The existing sites (llms-study.conf on :443/:80, tokenbay-kb.conf on :8443) are never edited.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DOMAIN="${VGEN_DOMAIN:-video.llm-pro.cn}"
CONF=/etc/nginx/conf.d/vgen.conf
ACME_ROOT=/var/www/vgen-acme

id vgenapp >/dev/null 2>&1 || useradd --system --home-dir /var/lib/vgen --create-home --shell /sbin/nologin vgenapp
mkdir -p /opt/vgen/web/dist /var/lib/vgen/data /etc/vgen "$ACME_ROOT"
chown vgenapp:vgenapp /var/lib/vgen /var/lib/vgen/data && chmod 750 /var/lib/vgen
chown root:vgenapp /etc/vgen && chmod 750 /etc/vgen
if [ ! -f /etc/vgen/vgen.env ]; then
  install -m 640 -o root -g vgenapp "$HERE/vgen.env.example" /etc/vgen/vgen.env
  echo "wrote /etc/vgen/vgen.env from the template"
fi
if grep -q '^TOKENBAY_API_KEY=.' /etc/vgen/vgen.env; then
  echo "/etc/vgen/vgen.env contains TOKENBAY_API_KEY: remove it, the hosted demo must not hold a key" >&2
  exit 1
fi
install -m 644 "$HERE/vgen.service" /etc/systemd/system/vgen.service
systemctl daemon-reload
systemctl enable vgen >/dev/null 2>&1 || true

if ! getent hosts "$DOMAIN" >/dev/null; then
  echo "DNS for $DOMAIN does not resolve yet: nginx and the certificate are skipped (add the A record, re-run)"
  exit 0
fi

if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ] && cmp -s "$HERE/nginx-vgen.conf" "$CONF"; then
  echo "nginx: $CONF unchanged"
  exit 0
fi
backup="/root/nginx-backup-$(date +%Y%m%d-%H%M%S).tgz"
tar -czf "$backup" -C /etc nginx
install_conf() {  # $1: config file to install as $CONF
  install -m 644 "$1" "$CONF"
  if nginx -t 2>/tmp/vgen-nginx-test.log; then
    systemctl reload nginx
  else
    cat /tmp/vgen-nginx-test.log >&2
    rm -f "$CONF"
    echo "nginx -t failed: $CONF removed again, nginx untouched (backup: $backup)" >&2
    exit 1
  fi
}
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  install_conf "$HERE/nginx-vgen-acme.conf"
  /opt/certbot/bin/certbot certonly --webroot -w "$ACME_ROOT" -d "$DOMAIN" --non-interactive --agree-tos \
    --keep-until-expiring --deploy-hook "systemctl reload nginx"
fi
install_conf "$HERE/nginx-vgen.conf"
echo "nginx: https://$DOMAIN -> 127.0.0.1:8790 (backup of /etc/nginx: $backup)"
