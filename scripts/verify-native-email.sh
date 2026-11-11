#!/usr/bin/env bash
# Rerun the native Resend test with already saved private credentials.
set -Eeuo pipefail
[[ "$(id -u)" -eq 0 ]] || { echo "Run with sudo/root." >&2; exit 1; }
[[ -f /etc/deltajanebi/native.env && -f /opt/deltajanebi/scripts/update-native.sh ]] || {
  echo "ERROR: Native DeltaJanebi installation not found." >&2
  exit 1
}
recipient="${1:-}"
if [[ ! "$recipient" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "ERROR: Give a valid recipient email address as the only argument." >&2
  exit 1
fi
echo "==> Updating DeltaJanebi native code; saved Resend key is preserved"
bash /opt/deltajanebi/scripts/update-native.sh
PYTHON=/opt/deltajanebi/.venv/bin/python
APP=/opt/deltajanebi
NATIVE_ENV=/etc/deltajanebi/native.env
echo "==> Checking Django imports without contacting Resend"
runuser -u deltajanebi -- "$PYTHON" \
  "$APP/scripts/native-env-exec.py" "$NATIVE_ENV" \
  "$PYTHON" "$APP/scripts/check-native-email.py" --check-imports
echo "==> Sending one test email with previously saved Resend settings"
runuser -u deltajanebi -- "$PYTHON" \
  "$APP/scripts/native-env-exec.py" "$NATIVE_ENV" \
  "$PYTHON" "$APP/scripts/check-native-email.py" "$recipient"
echo "✅ Provider accepted the test request. Check inbox or spam for delivery."
