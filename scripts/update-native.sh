#!/usr/bin/env bash
# Non-Docker native deployment update. Keeps PostgreSQL, media, backups, bot key.
set -Eeuo pipefail
umask 027

APP_DIR=/opt/deltajanebi
NATIVE_ENV=/etc/deltajanebi/native.env
APP_USER=deltajanebi

fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }
log(){ printf '\n==> %s\n' "$*"; }

[[ "$EUID" -eq 0 ]] || fail "Root required."
[[ -f "$NATIVE_ENV" && -d "$APP_DIR/.git" && -x "$APP_DIR/.venv/bin/python" ]] ||
  fail "Native DeltaJanebi installation missing. Run install-site.sh on a fresh server."
command -v systemctl >/dev/null || fail "systemd missing"
cd "$APP_DIR"

log "Backing up native configuration and PostgreSQL before updating"
install -d -m 700 /var/backups/deltajanebi
STAMP="$(date +%Y%m%d-%H%M%S)"
cp -a "$NATIVE_ENV" "/var/backups/deltajanebi/native-$STAMP.env"
if systemctl is-active --quiet postgresql && command -v pg_dump >/dev/null; then
  if ! runuser -u "$APP_USER" -- pg_dump -Fc deltajanebi >"/var/backups/deltajanebi/database-$STAMP.dump"; then
    rm -f "/var/backups/deltajanebi/database-$STAMP.dump"
    fail "PostgreSQL backup failed; refusing update."
  fi
  chmod 600 "/var/backups/deltajanebi/database-$STAMP.dump"
fi

log "Updating ONLY DeltaJanebi source"
git fetch --prune origin main
git reset --hard origin/main

log "Installing pinned Python dependencies without Docker"
export PIP_DISABLE_PIP_VERSION_CHECK=1
"$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir --prefer-binary --timeout 25 --retries 1 -r requirements.txt

log "Django migration, static files and configuration checks"
run_app(){
  runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" \
    "$APP_DIR/scripts/native-env-exec.py" "$NATIVE_ENV" \
    "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" "$@"
}
run_app migrate --noinput
run_app collectstatic --noinput
run_app check --fail-level ERROR

log "Restarting native lightweight Django and its necessary workers"
systemctl restart deltajanebi-web deltajanebi-syncjobs deltajanebi-reservations
if systemctl is-enabled --quiet deltajanebi-auto-sync; then
  systemctl restart deltajanebi-auto-sync
fi
systemctl restart caddy

# Check health directly before any external DNS/proxy.
DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' "$NATIVE_ENV" | tr -d '"' | head -n1)"
for attempt in $(seq 1 30); do
  if curl --silent --show-error --max-time 3 -o /dev/null \
    -H "Host: $DOMAIN_NOW" http://127.0.0.1:8000/health/ 2>/dev/null; then break; fi
  sleep 2
done
curl -fSs --max-time 12 -o /dev/null -H "Host: $DOMAIN_NOW" http://127.0.0.1:8000/health/ ||
  { journalctl -u deltajanebi-web --no-pager -n 80; fail "Django health check failed"; }
systemctl is-active --quiet deltajanebi-web || fail "Web service not running"
systemctl is-active --quiet deltajanebi-syncjobs || fail "Sync job supervisor not running"
systemctl is-active --quiet deltajanebi-reservations || fail "Reservation worker not running"
systemctl is-active --quiet caddy || fail "Caddy not running"

log "Updated native DeltaJanebi successfully. Docker not used."
printf 'Website: https://%s\n' "$DOMAIN_NOW"
printf 'Bot key file: /root/deltajanebi-bot-api-key.txt (content hidden)\n'
printf 'Services: systemctl status deltajanebi-web deltajanebi-syncjobs deltajanebi-reservations caddy\n'
