#!/usr/bin/env bash
# Tests that the local Caddy reverse proxy actually routes to Django over HTTPS.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' .env | head -n1)"
[[ -n "$DOMAIN_NOW" ]] || { echo "ERROR: DOMAIN is not configured" >&2; exit 1; }

echo "==> Checking Django HTTP response inside web container"
docker compose exec -T web python scripts/check_site.py

echo "==> Validating Caddy config"
docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

echo "==> Checking Caddy -> Django via local HTTPS (bypassing external DNS/CDN)"
ok=0
for attempt in 1 2 3 4 5 6; do
  if curl -kfsS --noproxy '*' --max-time 8 \
      --resolve "${DOMAIN_NOW}:443:127.0.0.1" \
      "https://${DOMAIN_NOW}/health/" >/dev/null 2>&1; then
    ok=1
    break
  fi
  echo "Local HTTPS check $attempt/6 did not succeed yet" >&2
  sleep 3
done
if [[ "$ok" != 1 ]]; then
  echo "ERROR: HTTPS through local Caddy failed. Check certificate issuance, 80/443, and Caddy logs." >&2
  exit 1
fi
echo "==> Local HTTPS health: OK"

echo "==> Checking that all deployment services are running"
for svc in db web syncjobs sync reservations caddy; do
  cid="$(docker compose ps -q --all "$svc")"
  if [[ -z "$cid" ]] || [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || true)" != true ]]; then
    echo "ERROR: service $svc is not running" >&2
    exit 1
  fi
done
echo "==> All services running"
