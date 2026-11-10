#!/usr/bin/env bash
# Safely continue an interrupted site install without removing any volumes or credentials.
set -Eeuo pipefail
APP_DIR="${DELTAJANEBI_APP_DIR:-/opt/deltajanebi}"
# The server uses /opt/deltajanebi. CI and manual checkouts can run in-place.
if [[ ! -d "$APP_DIR" && -f "./docker-compose.yml" ]]; then
  APP_DIR="$PWD"
fi
cd "$APP_DIR" || { echo "ERROR: Missing $APP_DIR" >&2; exit 1; }

on_error() {
  local status="$?" line="${1:-unknown}"
  trap - ERR
  printf '\n❌ Recovery stopped at line %s (exit=%s). No site data was removed.\n' "$line" "$status" >&2
  bash "$APP_DIR/scripts/site-diagnostics.sh" >&2 || true
  exit "$status"
}
trap 'on_error "$LINENO"' ERR

[[ -f .env ]] || { echo "ERROR: Missing .env; run install-site.sh first" >&2; exit 1; }
docker compose version >/dev/null
docker compose config -q

if docker image inspect deltajanebi-app:latest >/dev/null 2>&1; then
  echo "==> Existing app image found; skipping the build"
else
  echo "==> App image missing; building it with the host-network wheel installer"
  bash scripts/build-site-image.sh
fi

echo "==> Starting PostgreSQL"
docker compose up -d --no-build db

echo "==> Starting Django web without rebuilding"
docker compose up -d --no-build web

echo "==> Waiting for Django health"
healthy=0
for attempt in $(seq 1 75); do
  cid="$(docker compose ps -q --all web)"
  if [[ -n "$cid" ]]; then
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || true)"
    if [[ "$state" == healthy ]]; then healthy=1; break; fi
    if [[ "$state" == exited || "$state" == dead || "$state" == unhealthy ]]; then
      echo "ERROR: Web container state=$state" >&2
      docker compose logs --tail=100 web db >&2 || true
      exit 1
    fi
  fi
  sleep 2
done
if [[ "$healthy" != 1 ]]; then
  echo "ERROR: Django health check timed out" >&2
  docker compose logs --tail=100 web db >&2 || true
  exit 1
fi

echo "==> Checking homepage and bot API"
docker compose exec -T web python scripts/check_site.py
docker compose exec -T web python scripts/check_bot_api.py

echo "==> Starting Caddy and background workers"
docker compose up -d --no-build syncjobs sync reservations caddy
bash scripts/verify-deployment.sh

echo "✅ Site recovery completed and local HTTPS is healthy."
DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' .env | head -n1)"
echo "Website: https://${DOMAIN_NOW}"
echo "Bot key: /root/deltajanebi-bot-api-key.txt (value hidden)"
docker compose ps
