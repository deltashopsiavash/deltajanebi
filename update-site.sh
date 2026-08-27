#!/usr/bin/env bash
set -Eeuo pipefail

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
docker compose build --pull --no-cache web

echo "==> راه‌اندازی و recreate سرویس‌ها"
docker compose up -d db
docker compose up -d --force-recreate web sync reservations caddy

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
docker compose exec -T web python manage.py migrate --noinput
docker compose exec -T web python manage.py collectstatic --noinput --clear
docker compose exec -T web python manage.py check --fail-level ERROR

echo "==> تست API"
API_KEY="$(grep '^DELTAJANEBI_BOT_API_KEY=' .env | head -n1 | cut -d= -f2- || true)"
if [[ -z "$API_KEY" ]]; then
  API_KEY="$(openssl rand -hex 32)"
  printf '\nDELTAJANEBI_BOT_API_KEY=%s\n' "$API_KEY" >>.env
  printf '%s\n' "$API_KEY" >/root/deltajanebi-bot-api-key.txt
  chmod 600 /root/deltajanebi-bot-api-key.txt
  docker compose up -d --force-recreate web sync reservations
fi
docker compose exec -T web sh -lc 'curl -fsS --max-time 15 -X POST -H "Host: $DOMAIN" -H "Content-Type: application/json" -H "Authorization: Bearer $DELTAJANEBI_BOT_API_KEY" --data '\''{"action":"ping","payload":{}}'\'' http://127.0.0.1:8000/api/bot/v1/ | grep -q '\''"ok": true\|"ok":true'\'''

for svc in db web sync reservations caddy; do cid="$(docker compose ps -q --all "$svc")"; [[ -n "$cid" ]] || { echo "❌ $svc پیدا نشد."; exit 1; }; [[ "$(docker inspect -f '{{.State.Running}}' "$cid")" == true ]] || { docker compose logs --tail=120 "$svc" >&2 || true; exit 1; }; done

docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose restart caddy
docker image prune -f >/dev/null 2>&1 || true

echo "✅ سایت DeltaJanebi با موفقیت آپدیت شد. دیتابیس، media و .env حفظ شدند."
docker compose ps
