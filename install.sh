#!/usr/bin/env bash
set -Eeuo pipefail

REPO="https://github.com/deltashopsiavash/deltajanebi.git"
APP_DIR="/opt/deltajanebi"
TTY="/dev/tty"
export DEBIAN_FRONTEND=noninteractive

log() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nخطا: %s\n' "$*" >&2; exit 1; }

trap 'printf "\nنصب در خط %s متوقف شد.\n" "$LINENO" >&2' ERR

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  fail "این نصب باید با sudo/root اجرا شود."
fi

if [ ! -r "$TTY" ]; then
  fail "ترمینال تعاملی پیدا نشد. دستور نصب را داخل SSH/Terminal اجرا کن."
fi

prompt_required() {
  local __var="$1" __prompt="$2" __value=""
  while [ -z "$__value" ]; do
    IFS= read -r -p "$__prompt" __value <"$TTY"
  done
  printf -v "$__var" '%s' "$__value"
}

prompt_optional() {
  local __var="$1" __prompt="$2" __value=""
  IFS= read -r -p "$__prompt" __value <"$TTY" || true
  printf -v "$__var" '%s' "$__value"
}

prompt_secret_optional() {
  local __var="$1" __prompt="$2" __value=""
  IFS= read -r -s -p "$__prompt" __value <"$TTY" || true
  printf '\n' >"$TTY"
  printf -v "$__var" '%s' "$__value"
}

validate_domain() {
  local d="$1"
  [[ "$d" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]
}

validate_admin_ids() {
  [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]]
}

install_docker_official() {
  log "نصب Docker Engine + Compose + Buildx از مخزن رسمی Docker"

  apt-get update -y
  apt-get install -y ca-certificates curl git openssl gnupg

  if docker compose version >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then
    systemctl enable --now docker
    return 0
  fi

  # Remove packages that conflict with Docker CE, following Docker's Ubuntu install guidance.
  apt-get remove -y docker.io docker-doc docker-compose docker-compose-v2 docker-buildx podman-docker containerd runc >/dev/null 2>&1 || true

  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL --retry 3 --retry-delay 2 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc

  . /etc/os-release
  local codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
  [ -n "$codename" ] || fail "نسخه Ubuntu قابل تشخیص نیست."

  cat > /etc/apt/sources.list.d/docker.sources <<SOURCEEOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
SOURCEEOF
  rm -f /etc/apt/sources.list.d/docker.list

  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker

  docker version >/dev/null 2>&1 || fail "Docker Engine نصب نشد."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose نصب نشد."
  docker buildx version >/dev/null 2>&1 || fail "Docker Buildx نصب نشد."
}

install_docker_official
printf 'Docker: %s\n' "$(docker --version)"
printf 'Compose: %s\n' "$(docker compose version)"
printf 'Buildx: %s\n' "$(docker buildx version | head -n1)"

log "دریافت سورس پروژه"
if [ -e "$APP_DIR" ] && [ ! -d "$APP_DIR/.git" ]; then
  fail "$APP_DIR وجود دارد ولی Git repository نیست. آن را پاک یا منتقل کن و دوباره نصب را اجرا کن."
fi

if [ ! -d "$APP_DIR/.git" ]; then
  git clone --depth 1 "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin main
  git -C "$APP_DIR" reset --hard origin/main
fi
cd "$APP_DIR"

if [ ! -f .env ]; then
  log "تنظیم اولیه فروشگاه"

  while true; do
    prompt_required DOMAIN "دامنه بدون https:// (مثال shop.example.com): "
    DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"
    if validate_domain "$DOMAIN"; then break; fi
    printf 'دامنه معتبر نیست. مثال: shop.example.com\n' >"$TTY"
  done

  prompt_required BOT_TOKEN "توکن ربات تلگرام: "

  while true; do
    prompt_required ADMIN_IDS "آیدی عددی مدیر تلگرام (چند مدیر با ,): "
    if validate_admin_ids "$ADMIN_IDS"; then break; fi
    printf 'آیدی مدیر باید عددی باشد؛ چند آیدی را با کاما جدا کن.\n' >"$TTY"
  done

  prompt_optional EMAIL_USER "ایمیل ارسال بازیابی رمز (اختیاری، Enter برای رد شدن): "
  if [ -n "$EMAIL_USER" ]; then
    prompt_secret_optional EMAIL_PASS "App Password ایمیل: "
    EMAIL_PASS="${EMAIL_PASS// /}"
  else
    EMAIL_PASS=""
  fi

  DBPASS="$(openssl rand -hex 24)"
  SECRET="$(openssl rand -hex 48)"

  umask 077
  cat > .env <<ENVEOF
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
ENVEOF
  chmod 600 .env
fi

log "اعتبارسنجی Docker Compose"
docker compose config -q

log "ساخت image برنامه"
docker compose build --pull web

log "راه‌اندازی سرویس‌ها"
docker compose up -d --remove-orphans

log "بررسی سلامت سرویس وب"
WEB_ID="$(docker compose ps -q --all web)"
[ -n "$WEB_ID" ] || fail "کانتینر web ساخته نشد."

healthy=0
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  case "$status" in
    healthy) healthy=1; break ;;
    exited|dead|unhealthy)
      docker compose logs --tail=120 web >&2 || true
      fail "سرویس web سالم بالا نیامد."
      ;;
  esac
  sleep 2
done
[ "$healthy" -eq 1 ] || { docker compose logs --tail=120 web >&2 || true; fail "مهلت سلامت web تمام شد."; }

docker compose exec -T web python manage.py check --fail-level ERROR

# Give long-running workers a moment to fail fast on invalid configuration/token.
sleep 3
for svc in db web bot sync caddy; do
  cid="$(docker compose ps -q --all "$svc")"
  [ -n "$cid" ] || fail "کانتینر $svc ساخته نشد."
  running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    docker compose logs --tail=120 "$svc" >&2 || true
    fail "سرویس $svc در حال اجرا نیست."
  fi
done

cat > /usr/local/bin/deltajanebi-update <<'UPDATEEOF'
#!/usr/bin/env bash
set -Eeuo pipefail
exec /opt/deltajanebi/update.sh
UPDATEEOF
chmod +x /usr/local/bin/deltajanebi-update
chmod +x /opt/deltajanebi/update.sh

log "نصب کامل شد"
docker compose ps
printf '\nسایت: https://%s\n' "$(grep '^DOMAIN=' .env | cut -d= -f2-)"
printf 'برای آپدیت بعدی: sudo deltajanebi-update\n'
