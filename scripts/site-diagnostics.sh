#!/usr/bin/env bash
# Safe read-only diagnostics for a site that built successfully but cannot open.
# Never print .env contents or the bot API key.
set -uo pipefail
cd /opt/deltajanebi 2>/dev/null || {
  echo "ERROR: /opt/deltajanebi is not present" >&2
  exit 1
}

DOMAIN_NOW=""
if [[ -r .env ]]; then
  DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' .env | head -n1 | tr -d '\r')"
fi

echo "========== DELTAJANEBI PUBLIC ACCESS DIAGNOSTICS =========="
echo "Configured domain: ${DOMAIN_NOW:-missing}"
if [[ -s /root/deltajanebi-bot-api-key.txt ]]; then
  echo "Bot API key: present (hidden)"
else
  echo "Bot API key: missing"
fi

echo "--- Docker containers ---"
docker compose ps -a 2>&1 || true
echo "--- Server IPv4 addresses ---"
ip -4 -o addr show scope global 2>/dev/null | awk '{print $2, $4}' || true
echo "--- Listening ports 80 and 443 ---"
if command -v ss >/dev/null 2>&1; then
  ss -ltnp '( sport = :80 or sport = :443 )' 2>&1 || true
fi
if command -v ufw >/dev/null 2>&1; then
  echo "--- UFW firewall status ---"
  ufw status 2>&1 || true
fi

if [[ -n "$DOMAIN_NOW" ]]; then
  echo "--- DNS A records from this server ---"
  if ! getent ahostsv4 "$DOMAIN_NOW" 2>/dev/null | awk '{print $1}' | sort -u; then
    echo "DNS lookup failed."
  fi
  echo "--- DNS AAAA records from this server ---"
  getent ahostsv6 "$DOMAIN_NOW" 2>/dev/null | awk '{print $1}' | sort -u || true

  echo "--- Local Caddy HTTPS with certificate verification bypassed (routing test only) ---"
  if curl -kfsS --noproxy '*' --max-time 10 \
    --resolve "${DOMAIN_NOW}:443:127.0.0.1" \
    "https://${DOMAIN_NOW}/health/" >/dev/null 2>&1; then
    echo "PASS: local HTTPS routing to Django"
  else
    echo "FAIL: local HTTPS routing to Django"
  fi

  echo "--- Local Caddy HTTPS with strict certificate verification ---"
  if curl -fsS --noproxy '*' --max-time 10 \
    --resolve "${DOMAIN_NOW}:443:127.0.0.1" \
    "https://${DOMAIN_NOW}/health/" >/dev/null 2>&1; then
    echo "PASS: TLS certificate is trusted and Django responds"
  else
    echo "FAIL: invalid/missing TLS certificate or Django/Caddy response failure"
  fi

  echo "--- Public URL from this server (can fail if hairpin/CDN routing is blocked) ---"
  if curl -fsS --max-time 10 "https://${DOMAIN_NOW}/health/" >/dev/null 2>&1; then
    echo "PASS: domain resolves and returns valid HTTPS from this server"
  else
    echo "FAIL: cannot confirm public HTTPS (DNS, CDN, routing, certificate, or firewall)"
  fi
fi

echo "--- Caddy logs (last 60 lines) ---"
docker compose logs --no-color --tail=60 caddy 2>&1 || true
echo "--- Web logs (last 50 lines) ---"
docker compose logs --no-color --tail=50 web 2>&1 || true
echo "--- DB logs (last 20 lines) ---"
docker compose logs --no-color --tail=20 db 2>&1 || true
echo "========== END DIAGNOSTICS =========="
