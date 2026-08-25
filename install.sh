#!/usr/bin/env bash
set -euo pipefail
REPO="https://github.com/deltashopsiavash/deltajanebi.git"
APP_DIR="/opt/deltajanebi"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "این نصب باید با sudo/root اجرا شود."
  exit 1
fi

install_compose_fallback() {
  local arch asset
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) asset="x86_64" ;;
    aarch64|arm64) asset="aarch64" ;;
    *) echo "معماری ${arch} برای نصب خودکار Docker Compose پشتیبانی نشده است."; exit 1 ;;
  esac
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fL "https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-${asset}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
}

apt-get update
apt-get install -y ca-certificates curl git openssl

if ! command -v docker >/dev/null 2>&1; then
  apt-get install -y docker.io
fi
systemctl enable --now docker

if ! docker compose version >/dev/null 2>&1; then
  if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
    apt-get install -y docker-compose-v2
  elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
    apt-get install -y docker-compose-plugin
  else
    install_compose_fallback
  fi
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "خطا: Docker Compose نصب نشد."
  exit 1
fi

echo "Docker: $(docker --version)"
echo "Compose: $(docker compose version)"

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  read -rp "دامنه (مثال shop.example.com): " DOMAIN
  read -rp "توکن ربات تلگرام: " BOT_TOKEN
  read -rp "آیدی عددی مدیر تلگرام (چند مدیر با ,): " ADMIN_IDS
  read -rp "ایمیل ارسال بازیابی رمز (اختیاری): " EMAIL_USER
  if [ -n "$EMAIL_USER" ]; then
    read -rsp "رمز اپ ایمیل: " EMAIL_PASS
    echo
  else
    EMAIL_PASS=""
  fi

  DBPASS="$(openssl rand -hex 18)"
  SECRET="$(openssl rand -hex 32)"

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
