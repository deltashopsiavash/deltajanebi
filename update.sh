#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="/opt/deltajanebi"
cd "$APP_DIR"

install_updater_command() {
  cat > /usr/local/bin/deltajanebi-update <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
exec bash /opt/deltajanebi/update.sh
EOF
  chmod 755 /usr/local/bin/deltajanebi-update
}

# Repair the global updater command even if an older installation stopped
# before creating it.
install_updater_command

echo "==> دریافت نسخه جدید"
git fetch origin main
git reset --hard origin/main

# The reset above may have replaced this file with a newer version; keep the
# global command valid regardless of repository file mode.
install_updater_command

echo "==> بررسی تنظیمات"
docker compose config -q

echo "==> ساخت image جدید"
docker compose build --pull web

echo "==> اعمال آپدیت"
docker compose up -d --remove-orphans

WEB_ID="$(docker compose ps -q --all web)"
[ -n "$WEB_ID" ] || { echo "خطا: کانتینر web پیدا نشد." >&2; exit 1; }

healthy=0
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  if [ "$status" = "healthy" ]; then
    healthy=1
    break
  fi
  if [ "$status" = "unhealthy" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
    docker compose logs --tail=120 web >&2 || true
    exit 1
  fi
  sleep 2
done

[ "$healthy" -eq 1 ] || { docker compose logs --tail=120 web >&2 || true; exit 1; }
docker compose exec -T web python manage.py check --fail-level ERROR

for svc in db web bot sync caddy; do
  cid="$(docker compose ps -q --all "$svc")"
  [ -n "$cid" ] || { echo "خطا: کانتینر $svc پیدا نشد." >&2; exit 1; }
  running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    docker compose logs --tail=120 "$svc" >&2 || true
    echo "خطا: سرویس $svc در حال اجرا نیست." >&2
    exit 1
  fi
done

docker image prune -f >/dev/null 2>&1 || true

echo "آپدیت با موفقیت انجام شد."
echo "از این به بعد برای آپدیت فقط اجرا کن: sudo deltajanebi-update"
docker compose ps
