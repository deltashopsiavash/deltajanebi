#!/usr/bin/env bash
# Lightweight, production-oriented native install for freshly rebuilt Ubuntu.
# NO Docker, BuildKit, Compose or Docker daemon anywhere in this installer.
set -Eeuo pipefail
umask 027
export DEBIAN_FRONTEND=noninteractive

REPO=https://github.com/deltashopsiavash/deltajanebi.git
APP_DIR=/opt/deltajanebi
APP_USER=deltajanebi
NATIVE_ENV=/etc/deltajanebi/native.env
TTY=/dev/tty
HAS_TTY=0
[[ -t 1 ]] && HAS_TTY=1 || true

fail(){ printf '\n❌ %s\n' "$*" >&2; exit 1; }
log(){ printf '\n==> %s\n' "$*"; }
on_error(){
  local code="$?" line="$1"
  trap - ERR
  echo "❌ Native installer stopped at line $line (status $code)." >&2
  echo "No PostgreSQL database/media/backups were deleted." >&2
  echo "View logs: journalctl -u deltajanebi-web -n 70 --no-pager" >&2
  exit "$code"
}
trap 'on_error "$LINENO"' ERR

[[ "$EUID" -eq 0 ]] || fail "Run with sudo/root"
[[ -r /etc/os-release ]] || fail "Operating system identification missing"
. /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" == "24.04" ]] ||
  fail "For a predictable fresh install choose Ubuntu 24.04 LTS x86_64. Detected $PRETTY_NAME"
[[ "$(uname -m)" == x86_64 ]] || fail "Requires x86_64 (amd64)."
[[ -d /run/systemd/system ]] || fail "systemd is required"
if [[ -d "$APP_DIR/.git" && ! -f "$NATIVE_ENV" ]]; then
  fail "An existing non-native site was found. Refusing to overwrite its deployment."
fi
command -v curl >/dev/null || fail "curl missing. Ubuntu minimal image should include curl."
[[ "$HAS_TTY" == 1 || -n "${DELTA_SETUP_DOMAIN:-}" ]] ||
  fail "An interactive SSH session is required to ask for the site's domain."

ask(){
  local variable="$1" prompt="$2" default="$3" answer=""
  if [[ "$HAS_TTY" == 1 ]]; then
    read -r -p "$prompt [$default]: " answer <"$TTY" || true
  fi
  printf -v "$variable" '%s' "${answer:-$default}"
}
ask_secret(){
  local variable="$1" prompt="$2" value=""
  if [[ "$HAS_TTY" == 1 ]]; then
    read -r -s -p "$prompt: " value <"$TTY" || true
    printf '\n' >"$TTY"
  fi
  printf -v "$variable" '%s' "$value"
}
valid_domain(){ [[ "$1" =~ ^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }
env_line(){
  local name="$1" value="$2"
  # Quoted systemd EnvironmentFile format, also understood by Python shlex.
  value="${value//\\/\\\\}"
  value=${value//\"/\\\"}
  printf '%s="%s"\n' "$name" "$value" >>"$NATIVE_ENV"
}

if [[ -f "$NATIVE_ENV" ]]; then
  DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' "$NATIVE_ENV" | tr -d '"' | head -n1)"
  ADMIN_EMAIL="$(sed -n 's/^DJANGO_ADMIN_EMAIL=//p' "$NATIVE_ENV" | tr -d '"' | head -n1)"
  ADMIN_EMAIL="${ADMIN_EMAIL:-admin@$DOMAIN_NOW}"
  log "Existing native config found; reusing domain and credentials"
else
  log "Setup: native DeltaJanebi (no Docker)"
  DOMAIN_NOW="${DELTA_SETUP_DOMAIN:-}"
  if [[ -z "$DOMAIN_NOW" ]]; then
    while :; do
      ask DOMAIN_NOW "Site domain without https:// (example shop.example.com)" ""
      DOMAIN_NOW="${DOMAIN_NOW#http://}"
      DOMAIN_NOW="${DOMAIN_NOW#https://}"
      DOMAIN_NOW="${DOMAIN_NOW%%/*}"
      valid_domain "$DOMAIN_NOW" && break
      echo "Invalid domain; enter only a valid FQDN." >"$TTY"
    done
  fi
  if [[ "$DOMAIN_NOW" != localhost || "${DELTA_NATIVE_TEST_MODE:-0}" != 1 ]]; then
    valid_domain "$DOMAIN_NOW" || fail "Invalid domain"
  fi
  ask STORE_NAME "Store name" "دلتا جانبی"
  ask ADMIN_EMAIL "Site owner email for Django admin" "admin@$DOMAIN_NOW"
  [[ "$ADMIN_EMAIL" == *"@"* ]] || fail "Invalid admin email"
  if [[ -n "${DELTA_ADMIN_PASSWORD:-}" ]]; then
    ADMIN_PASS="$DELTA_ADMIN_PASSWORD"
  else
    ask_secret ADMIN_PASS "Choose admin password (hidden)"
  fi
  [[ "${#ADMIN_PASS}" -ge 10 ]] || fail "Administrator password must contain at least 10 characters."
  ask RESEND_FROM "Verified Resend sender email (optional)" "support@$DOMAIN_NOW"
  ask_secret RESEND_KEY "Resend SMTP/API key (leave blank to configure later)"
  [[ "$RESEND_FROM" == *"@"* ]] || fail "Invalid sender email"

  install -d -m 750 /etc/deltajanebi
  : > "$NATIVE_ENV"
  chmod 640 "$NATIVE_ENV"
  SECRET="$(openssl rand -hex 48)"
  BOT_KEY="$(openssl rand -hex 32)"
  env_line DOMAIN "$DOMAIN_NOW"
  env_line DJANGO_SECRET_KEY "$SECRET"
  env_line DEBUG 0
  env_line ALLOWED_HOSTS "$DOMAIN_NOW,www.$DOMAIN_NOW,localhost,127.0.0.1"
  env_line DATABASE_URL "postgresql:///deltajanebi?host=/var/run/postgresql"
  env_line DELTAJANEBI_BOT_API_KEY "$BOT_KEY"
  env_line STORE_NAME "$STORE_NAME"
  env_line DJANGO_ADMIN_EMAIL "$ADMIN_EMAIL"
  env_line SMTP_HOST smtp.resend.com
  env_line SMTP_PORT 587
  env_line SMTP_USER resend
  env_line SMTP_PASSWORD "$RESEND_KEY"
  env_line SMTP_USE_TLS 1
  env_line DEFAULT_FROM_EMAIL "$RESEND_FROM"
  env_line SOURCE_SYNC_INTERVAL 1800
  env_line DELTA_SOURCE_SUPERVISOR_POLL 3
  env_line DELTA_AUTO_SYNC_INITIAL_DELAY 600
  env_line TELEGRAM_BOT_TOKEN ""
  env_line TELEGRAM_ADMIN_IDS ""
  env_line TELEGRAM_STORE_CHAT_ID ""
fi

log "Memory preflight; set up persistent swap on low-memory VPS"
MEM_KB="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
FREE_KB="$(df --output=avail -k / | tail -1 | tr -d ' ')"
[[ "$FREE_KB" -gt 3500000 ]] || fail "Not enough free disk space for Python/PostgreSQL and safe swap (need >3.5GB)"
if (( MEM_KB < 1600000 )); then
  if ! swapon --noheadings --show=NAME | grep -q .; then
    log "Low RAM detected: creating 2GB swap before package installation"
    if [[ ! -f /swapfile-deltajanebi ]]; then
      fallocate -l 2G /swapfile-deltajanebi || dd if=/dev/zero of=/swapfile-deltajanebi bs=1M count=2048 status=none
    fi
    chmod 600 /swapfile-deltajanebi
    mkswap /swapfile-deltajanebi >/dev/null
    swapon /swapfile-deltajanebi
    grep -qF '/swapfile-deltajanebi none swap sw 0 0' /etc/fstab ||
      printf '/swapfile-deltajanebi none swap sw 0 0\n' >> /etc/fstab
  fi
  cat >/etc/sysctl.d/98-deltajanebi-memory.conf <<'EOF'
vm.swappiness=20
vm.vfs_cache_pressure=100
EOF
  sysctl -q --system >/dev/null 2>&1 || true
fi

log "Installing native Ubuntu dependencies: Python, PostgreSQL and Caddy"
apt-get update -y -o Acquire::Retries=1 -o Acquire::http::Timeout=25 -o Acquire::https::Timeout=25
if ! apt-cache show caddy >/dev/null 2>&1; then
  apt-get install -y software-properties-common
  add-apt-repository -y universe
  apt-get update -y -o Acquire::Retries=1 -o Acquire::http::Timeout=25 -o Acquire::https::Timeout=25
fi
apt-get install -y --no-install-recommends \
  ca-certificates curl git openssl python3 python3-venv python3-pip \
  postgresql postgresql-client caddy
systemctl enable --now postgresql

log "Creating dedicated unprivileged Linux account and PostgreSQL database"
if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin \
    --user-group "$APP_USER"
fi
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='$APP_USER'" postgres | grep -qx 1; then
  runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole "$APP_USER"
fi
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='deltajanebi'" postgres | grep -qx 1; then
  runuser -u postgres -- createdb --owner "$APP_USER" deltajanebi
fi

if (( MEM_KB < 1600000 )); then
  log "Tuning PostgreSQL for a 1GB VPS"
  runuser -u postgres -- psql -d postgres -v ON_ERROR_STOP=1 \
    -c "ALTER SYSTEM SET shared_buffers = '32MB';" \
    -c "ALTER SYSTEM SET work_mem = '2MB';" \
    -c "ALTER SYSTEM SET maintenance_work_mem = '32MB';" \
    -c "ALTER SYSTEM SET max_connections = '30';"
  systemctl restart postgresql
fi
runuser -u "$APP_USER" -- psql -d deltajanebi -Atqc "SELECT 1" | grep -qx 1 ||
  fail "PostgreSQL local authentication failed"

log "Fetching DeltaJanebi source from GitHub (SanaShop not touched)"
if [[ -e "$APP_DIR" && ! -d "$APP_DIR/.git" ]]; then
  fail "$APP_DIR exists without Git; no data will be deleted automatically"
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone --depth 1 "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" fetch --prune origin main
  git -C "$APP_DIR" reset --hard origin/main
fi

log "Installing pinned Python requirements in venv (no Docker)"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi
if ! "$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir --prefer-binary --timeout 25 --retries 1 \
    -i https://mirrors.aliyun.com/pypi/simple/ -r "$APP_DIR/requirements.txt"; then
  log "Primary Python mirror failed; trying official PyPI"
  "$APP_DIR/.venv/bin/python" -m pip install --no-cache-dir --prefer-binary --timeout 25 --retries 1 \
    -i https://pypi.org/simple/ -r "$APP_DIR/requirements.txt"
fi

log "Preparing private configuration, media, static and backup folders"
# Make the private directory traversable by the Django service user.
chown root:"$APP_USER" /etc/deltajanebi
chmod 750 /etc/deltajanebi
install -d -m 750 -o "$APP_USER" -g "$APP_USER" \
  "$APP_DIR/media" "$APP_DIR/backups"
install -d -m 755 -o "$APP_USER" -g "$APP_USER" "$APP_DIR/staticfiles"
chown root:"$APP_USER" "$NATIVE_ENV"
chmod 640 "$NATIVE_ENV"
# Keep the legacy public file path for scripts that expect .env, without
# copying any service credentials to a public location.
cp -a "$NATIVE_ENV" "$APP_DIR/.env"
chown root:"$APP_USER" "$APP_DIR/.env"
chmod 640 "$APP_DIR/.env"

run_django(){
  runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" \
    "$APP_DIR/scripts/native-env-exec.py" "$NATIVE_ENV" \
    "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" "$@"
}
log "Migrating database and collecting static assets"
run_django migrate --noinput
run_django collectstatic --noinput
run_django check --fail-level ERROR
run_django shell -c 'import os; from shop.models import SiteSetting; s=SiteSetting.load(); name=os.getenv("STORE_NAME","").strip(); s.store_name=name or s.store_name; s.save(update_fields=["store_name"]); print("Store branding initialized")'

if [[ -n "${ADMIN_PASS:-}" ]]; then
  log "Creating site owner (password hidden)"
  printf '%s\n' "$ADMIN_PASS" | runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" \
    "$APP_DIR/scripts/native-env-exec.py" "$NATIVE_ENV" \
    "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" shell -c \
    'import os,sys; from django.contrib.auth import get_user_model; u=get_user_model(); email=os.environ["DJANGO_ADMIN_EMAIL"]; password=sys.stdin.readline().rstrip("\n"); obj,created=u.objects.get_or_create(email=email, defaults={"is_superuser":True,"is_staff":True,"is_active":True}); obj.is_superuser=True; obj.is_staff=True; obj.is_active=True; obj.set_password(password); obj.save(); print("Site owner ready (password hidden)")'
  unset ADMIN_PASS
fi

log "Creating low-footprint systemd services"
cat >/etc/systemd/system/deltajanebi-web.service <<EOF
[Unit]
Description=DeltaJanebi lightweight Django web (no Docker)
After=network-online.target postgresql.service
Wants=network-online.target
Requires=postgresql.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$NATIVE_ENV
ExecStart=$APP_DIR/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 1 --threads 2 --worker-class gthread --timeout 60 --max-requests 500 --max-requests-jitter 50 --log-level warning
Restart=always
RestartSec=3
UMask=0027
NoNewPrivileges=yes
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/deltajanebi-syncjobs.service <<EOF
[Unit]
Description=DeltaJanebi manual product-sync supervisor (lightweight)
After=deltajanebi-web.service
Requires=deltajanebi-web.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$NATIVE_ENV
ExecStart=$APP_DIR/.venv/bin/python manage.py source_sync_supervisor
Restart=always
RestartSec=10
UMask=0027
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/deltajanebi-reservations.service <<EOF
[Unit]
Description=DeltaJanebi order reservation expiration service
After=deltajanebi-web.service
Requires=deltajanebi-web.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$NATIVE_ENV
ExecStart=$APP_DIR/.venv/bin/python manage.py reservation_loop
Restart=always
RestartSec=10
UMask=0027
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/deltajanebi-auto-sync.service <<EOF
[Unit]
Description=DeltaJanebi optional periodic source sync (OFF by default on 1GB)
After=deltajanebi-web.service
Requires=deltajanebi-web.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$NATIVE_ENV
ExecStart=$APP_DIR/.venv/bin/python manage.py sync_loop
Restart=always
RestartSec=15
UMask=0027
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF

log "Configuring system Caddy HTTPS reverse proxy"
# Existing machine is assumed freshly rebuilt; back up the distro Caddyfile.
if [[ -f /etc/caddy/Caddyfile ]]; then
  cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.before-deltajanebi.$(date +%Y%m%d%H%M%S)"
fi
cat >/etc/caddy/Caddyfile <<EOF
{
    email $ADMIN_EMAIL
}

$DOMAIN_NOW {
    encode zstd gzip
    handle_path /static/* {
        root * $APP_DIR/staticfiles
        file_server
    }
    handle_path /media/* {
        root * $APP_DIR/media
        file_server
    }
    reverse_proxy 127.0.0.1:8000 {
        transport http {
            dial_timeout 4s
            response_header_timeout 60s
        }
    }
}
EOF
# Allow the Caddy service to read only the public media file tree.
usermod -aG "$APP_USER" caddy
chmod 755 "$APP_DIR" "$APP_DIR/media" "$APP_DIR/staticfiles"
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
systemctl daemon-reload
systemctl enable --now deltajanebi-web deltajanebi-syncjobs deltajanebi-reservations
systemctl disable --now deltajanebi-auto-sync >/dev/null 2>&1 || true
systemctl enable --now caddy
systemctl restart caddy

if command -v ufw >/dev/null && ufw status | grep -q "^Status: active"; then
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi

log "Verifying all live services and internal Django+API responses"
for _ in $(seq 1 30); do
  if curl -fsS --silent --max-time 3 -H "Host: $DOMAIN_NOW" \
      http://127.0.0.1:8000/health/ >/dev/null 2>&1; then break; fi
  sleep 2
done
curl -fsS --max-time 10 -H "Host: $DOMAIN_NOW" \
  http://127.0.0.1:8000/health/ >/dev/null ||
  { journalctl -u deltajanebi-web -n 80 --no-pager; fail "Django did not pass health check"; }
for service in postgresql deltajanebi-web deltajanebi-syncjobs deltajanebi-reservations caddy; do
  systemctl is-active --quiet "$service" || fail "Service failed: $service"
done
# The API key is never printed as part of this health check.
runuser -u "$APP_USER" -- "$APP_DIR/.venv/bin/python" \
  "$APP_DIR/scripts/native-env-exec.py" "$NATIVE_ENV" \
  "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/check_bot_api.py"

install -d -m 700 /root
API_KEY_NOW="$(sed -n 's/^DELTAJANEBI_BOT_API_KEY=//p' "$NATIVE_ENV" | tr -d '"' | head -n1)"
printf '%s\n' "$API_KEY_NOW" >/root/deltajanebi-bot-api-key.txt
chmod 600 /root/deltajanebi-bot-api-key.txt

cat >/usr/local/bin/deltajanebi-update <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/update-site.sh | sudo bash
EOF
chmod 755 /usr/local/bin/deltajanebi-update

printf '\n✅ Native DeltaJanebi installed. ZERO Docker containers required.\n'
printf 'Website: https://%s\n' "$DOMAIN_NOW"
printf 'Admin: https://%s/admin/\n' "$DOMAIN_NOW"
printf 'Bot API key saved ONLY to /root/deltajanebi-bot-api-key.txt\n'
printf 'Update command: sudo deltajanebi-update\n'
printf 'Low RAM mode: periodic AUTO sync disabled; MANUAL sync via bot stays enabled.\n'
printf 'Enable periodic auto sync when RAM is upgraded: sudo systemctl enable --now deltajanebi-auto-sync\n'
printf 'Site services: systemctl status deltajanebi-web deltajanebi-syncjobs deltajanebi-reservations caddy\n'
if ! curl -kfsS --noproxy '*' --max-time 8 \
    --resolve "$DOMAIN_NOW:443:127.0.0.1" \
    "https://$DOMAIN_NOW/health/" >/dev/null 2>&1; then
  printf 'WARNING: Public HTTPS not verified yet. Point domain DNS to server IPv4 and open 80/443, then check Caddy logs.\n'
  printf 'Check: journalctl -u caddy -n 60 --no-pager\n'
fi
if [[ -z "${RESEND_KEY:-}" ]]; then
  echo "WARNING: Resend key was blank; email verification will require adding SMTP_PASSWORD to the private native.env."
fi
