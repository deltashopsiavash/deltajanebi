#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="/opt/deltajanebi"
cd "$APP_DIR"

echo "==> دریافت نسخه جدید"
git fetch origin main
git reset --hard origin/main

echo "==> بررسی تنظیمات"
docker compose config -q

echo "==> ساخت image جدید"
docker compose build --pull web

echo "==> اعمال آپدیت"
docker compose up -d --remove-orphans

WEB_ID="$(docker compose ps -q --all web)"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$WEB_ID" 2>/dev/null || true)"
  [ "$status" = "healthy" ] && break
  if [ "$status" = "unhealthy" ] || [ "$status" = "exited" ] || [ "$status" = "dead" ]; then
    docker compose logs --tail=120 web >&2 || true
    exit 1
  fi
  sleep 2
done

[ "$(docker inspect -f '{{.State.Health.Status}}' "$WEB_ID")" = "healthy" ] || { docker compose logs --tail=120 web >&2 || true; exit 1; }
docker compose exec -T web python manage.py check --fail-level ERROR
docker image prune -f >/dev/null 2>&1 || true

echo "آپدیت با موفقیت انجام شد."
docker compose ps
