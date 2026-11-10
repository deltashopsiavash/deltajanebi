#!/usr/bin/env bash
# Safe installation diagnostics. Never print .env or connection secrets.
set -uo pipefail
cd /opt/deltajanebi 2>/dev/null || {
  echo "Source directory /opt/deltajanebi is not present." >&2
  exit 0
}

echo
echo "========== DELTAJANEBI INSTALL DIAGNOSTICS =========="
if [[ -s /root/deltajanebi-bot-api-key.txt ]]; then
  echo "Bot API key: present in /root/deltajanebi-bot-api-key.txt (value hidden)"
else
  echo "Bot API key: missing / empty"
fi
echo "--- Docker service state ---"
docker compose ps -a 2>&1 || true

if command -v ss >/dev/null 2>&1; then
  echo "--- Listeners on ports 80 and 443 ---"
  ss -ltnp '( sport = :80 or sport = :443 )' 2>&1 || true
fi

for service in db web caddy syncjobs sync reservations; do
  echo "--- $service logs (last 40 lines) ---"
  docker compose logs --no-color --tail=40 "$service" 2>&1 || true
done

if [[ -r .env ]]; then
  DOMAIN_NOW="$(sed -n 's/^DOMAIN=//p' .env | head -n1)"
  if [[ -n "$DOMAIN_NOW" ]]; then
    echo "--- DNS IPv4 for configured domain ---"
    getent ahostsv4 "$DOMAIN_NOW" 2>/dev/null | awk 'NR <= 3 {print $1}' || true
  fi
fi
echo "========== END DIAGNOSTICS =========="
