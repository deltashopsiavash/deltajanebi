#!/usr/bin/env bash
# Recover Resend 403 caused by the changed sender default on native installs.
set -Eeuo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "Root/sudo required." >&2; exit 1; }
[[ -f /etc/deltajanebi/native.env ]] || { echo "Native DeltaJanebi not found." >&2; exit 1; }
recipient="${1:-}"
if [[ ! "$recipient" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Usage: sudo bash -s -- recipient@example.com" >&2
  exit 1
fi
echo "==> Updating only native DeltaJanebi, leaving SanaShop untouched"
bash /opt/deltajanebi/scripts/update-native.sh
echo "==> Testing the old mail.DOMAIN sender; changes apply only after accepted test"
exec /opt/deltajanebi/.venv/bin/python \
  /opt/deltajanebi/scripts/recover-native-resend-sender.py "$recipient"
