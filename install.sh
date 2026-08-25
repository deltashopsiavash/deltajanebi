#!/usr/bin/env bash
set -euo pipefail
REPO="https://github.com/deltashopsiavash/deltajanebi.git"
APP_DIR="/opt/deltajanebi"
if [ "${EUID:-$(id -u)}" -ne 0 ]; then echo "این نصب باید با sudo/root اجرا شود."; exit 1; fi
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl git docker.io docker-compose-plugin openssl
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then apt-get update && apt-get install -y docker-compose-plugin; fi
if [ ! -d "$APP_DIR/.git" ]; then git clone "$REPO" "$APP_DIR"; else git -C "$APP_DIR" pull --ff-only; fi
cd "$APP_DIR"
if [ ! -f .env ]; then
  read -rp "دامنه (مثال shop.example.com): " DOMAIN
  read -rp "توکن ربات تلگرام: " BOT_TOKEN
  read -rp "آیدی عددی مدیر تلگرام (چند مدیر با ,): " ADMIN_IDS
  read -rp "ایمیل ارسال بازیابی رمز (اختیاری): " EMAIL_USER
  if [ -n "$EMAIL_USER" ]; then read -rsp "رمز اپ ایمیل: " EMAIL_PASS; echo; else EMAIL_PASS=""; fi
  DBPASS="$(openssl rand -hex 18)"; SECRET="$(openssl rand -hex 32)"
  cat > .env <<EOF
DOMAIN=${DOMAIN}
DJANGO_SECRET_KEY=${SECRET}
DEBUG=0
ALLOWED_HOSTS=${DOMAIN},localhost,127.0.0.1
POSTGRES_DB=deltajanebi
POSTGRES_USER=deltajanebi
POSTGRES_PASSWORD=${DBPASS}
DATABASE_URL=postgresql://deltajanebi:${DBPASS}@db:5432/deltajanebi
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${ADMIN_IDS}
TELEGRAM_STORE_CHAT_ID=${ADMIN_IDS%%,*}
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=${EMAIL_USER}
EMAIL_HOST_PASSWORD=${EMAIL_PASS}
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=${EMAIL_USER}
SOURCE_ALLOWED_HOSTS=hamrahedovom.ir,www.hamrahedovom.ir
SOURCE_SYNC_INTERVAL=1800
SOURCE_SYNC_DELAY=1.2
SOURCE_REQUEST_TIMEOUT=20
SOURCE_USER_AGENT=DeltaJanebiSync/1.0
STORE_NAME=دلتا جانبی
STORE_PHONE=
STORE_CARD_NUMBER=
STORE_CARD_OWNER=
EOF
fi
docker compose up -d --build
cat >/usr/local/bin/deltajanebi-update <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/deltajanebi
git pull --ff-only
docker compose up -d --build --remove-orphans
docker image prune -f >/dev/null 2>&1 || true
echo "DeltaJanebi updated."
EOF
chmod +x /usr/local/bin/deltajanebi-update
echo "نصب انجام شد. برای آپدیت بعدی فقط اجرا کن: sudo deltajanebi-update"
