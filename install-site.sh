#!/usr/bin/env bash
set -Eeuo pipefail

REPO="https://github.com/deltashopsiavash/deltajanebi.git"
APP_DIR="/opt/deltajanebi"
TTY="/dev/tty"
export DEBIAN_FRONTEND=noninteractive

log(){ printf '\n==> %s\n' "$*"; }
fail(){ printf '\n❌ %s\n' "$*" >&2; exit 1; }
trap 'printf "\n❌ نصب در خط %s متوقف شد.\n" "$LINENO" >&2' ERR

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "این دستور باید با sudo/root اجرا شود."
[[ -r "$TTY" ]] || fail "ترمینال تعاملی پیدا نشد. دستور را داخل SSH/Terminal اجرا کن."

ask(){ local var="$1" prompt="$2" def="${3:-}" value=""; if [[ -n "$def" ]]; then read -r -p "$prompt [$def]: " value <"$TTY" || true; value="${value:-$def}"; else while [[ -z "$value" ]]; do read -r -p "$prompt: " value <"$TTY"; done; fi; printf -v "$var" '%s' "$value"; }
ask_secret(){ local var="$1" prompt="$2" value=""; while [[ -z "$value" ]]; do read -r -s -p "$prompt: " value <"$TTY"; printf '\n' >"$TTY"; done; printf -v "$var" '%s' "$value"; }
valid_domain(){ [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]; }

install_docker(){
  log "نصب Docker Engine و Compose"
  apt-get update -y
  apt-get install -y ca-certificates curl git openssl gnupg
  if docker compose version >/dev/null 2>&1 && docker buildx version >/dev/null 2>&1; then systemctl enable --now docker; return; fi
  apt-get remove -y docker.io docker-doc docker-compose docker-compose-v2 docker-buildx podman-docker containerd runc >/dev/null 2>&1 || true
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL --retry 4 --retry-delay 2 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"; [[ -n "$codename" ]] || fail "نسخه Ubuntu قابل تشخیص نیست."
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  rm -f /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
  docker compose version >/dev/null || fail "Docker Compose نصب نشد."
}

install_docker

log "دریافت سورس DeltaJanebi"
if [[ -e "$APP_DIR" && ! -d "$APP_DIR/.git" ]]; then fail "$APP_DIR وجود دارد ولی Git repository نیست؛ آن را منتقل/پاک کن و دوباره نصب را اجرا کن."; fi
if [[ ! -d "$APP_DIR/.git" ]]; then git clone --depth 1 "$REPO" "$APP_DIR"; else git -C "$APP_DIR" fetch --prune origin main; git -C "$APP_DIR" reset --hard origin/main; fi
cd "$APP_DIR"

if [[ ! -f .env ]]; then
  log "تنظیم اولیه سایت"
  while true; do ask DOMAIN "دامنه بدون https:// (مثال shop.example.com)"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN%%/*}"; valid_domain "$DOMAIN" && break; echo "دامنه معتبر نیست." >"$TTY"; done
  ask ACME_EMAIL "ایمیل برای SSL" "admin@$DOMAIN"
  ask STORE_NAME "نام فروشگاه" "دلتا جانبی"

  echo >"$TTY"
  echo "Resend باید دامنه ارسال شما را Verified نشان بدهد؛ DNS را در Cloudflare/ارائه‌دهنده خودت ثبت کن." >"$TTY"
  echo "برای این سایت می‌توانی مثلا mail.$DOMAIN را در Resend اضافه کنی." >"$TTY"
  ask RESEND_DOMAIN "دامنه ارسال Resend" "mail.$DOMAIN"
  ask_secret RESEND_API_KEY "Resend API Key"
  ask RESEND_FROM_EMAIL "ایمیل فرستنده" "support@$RESEND_DOMAIN"

  DBPASS="$(openssl rand -hex 24)"
  SECRET="$(openssl rand -hex 48)"
  BOT_API_KEY="$(openssl rand -hex 32)"

  umask 077
  cat >.env <<EOF
DOMAIN=$DOMAIN
ACME_EMAIL=$ACME_EMAIL
DJANGO_SECRET_KEY=$SECRET
DEBUG=0
ALLOWED_HOSTS=$DOMAIN,www.$DOMAIN,localhost,127.0.0.1
POSTGRES_DB=deltajanebi
POSTGRES_USER=deltajanebi
POSTGRES_PASSWORD=$DBPASS
DATABASE_URL=postgresql://deltajanebi:$DBPASS@db:5432/deltajanebi
DELTAJANEBI_BOT_API_KEY=$BOT_API_KEY
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=resend
SMTP_PASSWORD=$RESEND_API_KEY
SMTP_USE_TLS=1
DEFAULT_FROM_EMAIL=$STORE_NAME <$RESEND_FROM_EMAIL>
SOURCE_ALLOWED_HOSTS=
SOURCE_SYNC_INTERVAL=1800
SOURCE_SYNC_DELAY=1.2
SOURCE_REQUEST_TIMEOUT=20
SOURCE_USER_AGENT=DeltaJanebiSync/2.0
SOURCE_BRAND_TERMS=
STORE_NAME=$STORE_NAME
STORE_PHONE=
STORE_CARD_NUMBER=
STORE_CARD_OWNER=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_IDS=
TELEGRAM_STORE_CHAT_ID=
EOF
  chmod 600 .env
else
  log ".env موجود است؛ تنظیمات و کلیدهای قبلی حفظ شدند."
fi

API_KEY="$(grep '^DELTAJANEBI_BOT_API_KEY=' .env | head -n1 | cut -d= -f2- || true)"
if [[ -z "$API_KEY" ]]; then
  API_KEY="$(openssl rand -hex 32)"
  printf '\nDELTAJANEBI_BOT_API_KEY=%s\n' "$API_KEY" >> .env
fi
chmod 600 .env
printf '%s\n' "$API_KEY" >/root/deltajanebi-bot-api-key.txt
chmod 600 /root/deltajanebi-bot-api-key.txt

log "اعتبارسنجی Compose و ساخت کامل image"
docker compose config -q
docker compose build --pull --no-cache web

log "راه‌اندازی دیتابیس، سایت، Sync، رزرو و Caddy"
docker compose up -d db
docker compose up -d --force-recreate web sync reservations caddy

WEB_ID="$(docker compose ps -q --all web)"; [[ -n "$WEB_ID" ]] || fail "کانتینر web ساخته نشد."
healthy=0
for _ in $(seq 1 75); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  [[ "$status" == healthy ]] && { healthy=1; break; }
  if [[ "$status" =~ ^(exited|dead|unhealthy)$ ]]; then docker compose logs --tail=160 web db >&2 || true; fail "سرویس web سالم بالا نیامد."; fi
  sleep 2
done
[[ "$healthy" -eq 1 ]] || { docker compose logs --tail=160 web >&2 || true; fail "مهلت سلامت web تمام شد."; }

log "اعمال migration، static و تنظیم نام فروشگاه"
docker compose exec -T web python manage.py migrate --noinput
docker compose exec -T web python manage.py collectstatic --noinput
docker compose exec -T web python manage.py shell -c 'import os; from shop.models import SiteSetting; s=SiteSetting.load(); name=os.getenv("STORE_NAME","").strip(); phone=os.getenv("STORE_PHONE","").strip(); card=os.getenv("STORE_CARD_NUMBER","").strip(); owner=os.getenv("STORE_CARD_OWNER","").strip(); fields=[]; [(setattr(s,f,v),fields.append(f)) for f,v in (("store_name",name),("phone",phone),("card_number",card),("card_owner",owner)) if v]; s.save(update_fields=fields) if fields else None; print("store bootstrap: OK")'
docker compose exec -T web python manage.py check --fail-level ERROR

log "تست API اختصاصی DeltaJanebi"
docker compose exec -T web sh -lc 'curl -fsS --max-time 15 -X POST -H "Host: $DOMAIN" -H "Content-Type: application/json" -H "Authorization: Bearer $DELTAJANEBI_BOT_API_KEY" --data '\''{"action":"ping","payload":{}}'\'' http://127.0.0.1:8000/api/bot/v1/ | grep -q '\''"ok": true\|"ok":true'\'''

for svc in db web sync reservations caddy; do cid="$(docker compose ps -q --all "$svc")"; [[ -n "$cid" ]] || fail "سرویس $svc ساخته نشد."; running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)"; [[ "$running" == true ]] || { docker compose logs --tail=120 "$svc" >&2 || true; fail "سرویس $svc در حال اجرا نیست."; }; done

cat >/usr/local/bin/deltajanebi-update <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
curl -fsSL https://raw.githubusercontent.com/deltashopsiavash/deltajanebi/main/update-site.sh | sudo bash
EOF
chmod 755 /usr/local/bin/deltajanebi-update

DOMAIN_NOW="$(grep '^DOMAIN=' .env | cut -d= -f2-)"
printf '\n✅ نصب سایت DeltaJanebi کامل شد.\n'
printf '🌐 سایت: https://%s\n' "$DOMAIN_NOW"
printf '🔐 کلید اتصال ربات در این فایل امن ذخیره شد: /root/deltajanebi-bot-api-key.txt\n'
printf 'برای دیدن کلید فقط روی خود سرور: sudo cat /root/deltajanebi-bot-api-key.txt\n'
printf 'برای آپدیت‌های بعدی: sudo deltajanebi-update\n'
printf '\nبعد از نصب ربات خارج، داخل تلگرام /start → اتصال سایت → URL سایت → همین کلید را وارد کن.\n'
docker compose ps
