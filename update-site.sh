#!/usr/bin/env bash
set -Eeuo pipefail

# Native deployments update without Docker, while older Docker installs stay unchanged.
if [[ -f /etc/deltajanebi/native.env ]]; then
  [[ -r /opt/deltajanebi/scripts/update-native.sh ]] || {
    echo "ERROR: Native updater missing; checkout incomplete." >&2
    exit 1
  }
  exec bash /opt/deltajanebi/scripts/update-native.sh
fi

[[ ${EUID} -eq 0 ]] || { echo "این دستور باید با sudo/root اجرا شود."; exit 1; }
APP_DIR="/opt/deltajanebi"
[[ -d "$APP_DIR/.git" ]] || { echo "❌ DeltaJanebi در $APP_DIR نصب نیست. اول install-site.sh را اجرا کن."; exit 1; }
cd "$APP_DIR"
[[ -f .env ]] || { echo "❌ فایل .env پیدا نشد؛ آپدیت متوقف شد تا تنظیماتت از بین نرود."; exit 1; }

cp -a .env "/root/deltajanebi-env-before-update-$(date +%Y%m%d-%H%M%S)"

echo "==> دریافت نسخه جدید"
git remote set-url origin https://github.com/deltashopsiavash/deltajanebi.git
git fetch --prune origin main
git reset --hard origin/main

echo "==> نسخه"
git log -1 --oneline

echo "==> اعتبارسنجی Compose"
docker compose config -q

echo "==> ساخت تمیز image"
bash scripts/build-site-image.sh

echo "==> راه‌اندازی و recreate سرویس‌ها"
docker compose up -d db
docker compose up -d --no-build --force-recreate web

WEB_ID="$(docker compose ps -q --all web)"
[[ -n "$WEB_ID" ]] || { echo "❌ web ساخته نشد."; exit 1; }
healthy=0
for _ in $(seq 1 75); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  [[ "$status" == healthy ]] && { healthy=1; break; }
  if [[ "$status" =~ ^(exited|dead|unhealthy)$ ]]; then docker compose logs --tail=180 web db >&2 || true; exit 1; fi
  sleep 2
done
[[ "$healthy" -eq 1 ]] || { docker compose logs --tail=180 web >&2 || true; echo "❌ web سالم نشد."; exit 1; }

echo "==> migration + static + check"
docker compose exec --interactive=false -T web python manage.py migrate --noinput
docker compose exec --interactive=false -T web python manage.py collectstatic --noinput --clear
docker compose exec --interactive=false -T web python manage.py check --fail-level ERROR

echo "==> تست API"
API_KEY="$(grep '^DELTAJANEBI_BOT_API_KEY=' .env | head -n1 | cut -d= -f2- || true)"
if [[ -z "$API_KEY" ]]; then
  API_KEY="$(openssl rand -hex 32)"
  printf '\nDELTAJANEBI_BOT_API_KEY=%s\n' "$API_KEY" >>.env
  printf '%s\n' "$API_KEY" >/root/deltajanebi-bot-api-key.txt
  chmod 600 /root/deltajanebi-bot-api-key.txt
  docker compose up -d --no-build --force-recreate web syncjobs sync reservations
fi
docker compose exec --interactive=false -T web python scripts/check_bot_api.py
docker compose exec --interactive=false -T web python scripts/check_site.py

echo "==> Starting Caddy and background workers"
docker compose up -d --no-build --force-recreate syncjobs sync reservations caddy
if ! bash scripts/verify-deployment.sh; then
  bash scripts/site-diagnostics.sh >&2 || true
  exit 1
fi

for svc in db web syncjobs sync reservations caddy; do cid="$(docker compose ps -q --all "$svc")"; [[ -n "$cid" ]] || { echo "❌ $svc پیدا نشد."; exit 1; }; [[ "$(docker inspect -f '{{.State.Running}}' "$cid")" == true ]] || { docker compose logs --tail=120 "$svc" >&2 || true; exit 1; }; done

echo "==> تست supervisor همگام‌سازی"
docker compose logs --tail=40 syncjobs | grep -q 'catalog supervisor v26 is ready\|Delta source catalog supervisor v26 is ready' || {
  docker compose logs --tail=120 syncjobs >&2 || true
  echo "❌ supervisor همگام‌سازی آماده نشد."
  exit 1
}

# Caddy was already validated and tested by verify-deployment.sh.
docker image prune -f >/dev/null 2>&1 || true

echo "✅ سایت DeltaJanebi با موفقیت آپدیت شد. دیتابیس، media و .env حفظ شدند."
docker compose ps
