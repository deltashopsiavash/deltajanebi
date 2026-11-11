#!/usr/bin/env bash
# One-command repair: update native Delta code (no Docker), securely configure Resend.
set -Eeuo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root/sudo." >&2; exit 1; }
[[ -f /etc/deltajanebi/native.env ]] || {
  echo "Native DeltaJanebi installation is not present." >&2
  exit 1
}
[[ -f /opt/deltajanebi/scripts/update-native.sh ]] || {
  echo "Native DeltaJanebi updater missing." >&2
  exit 1
}
echo "==> Update DeltaJanebi native email support (SanaShop untouched)"
bash /opt/deltajanebi/scripts/update-native.sh
echo "==> Configure Resend credentials privately and send a real test email"
exec /opt/deltajanebi/.venv/bin/python /opt/deltajanebi/scripts/configure-native-email.py
