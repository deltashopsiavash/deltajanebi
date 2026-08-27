#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "این دستور باید با sudo/root اجرا شود."; exit 1; }
APP_DIR="/opt/deltajanebi-bot"
RUNTIME_DIR="/opt/deltajanebi-bot-runtime"
ENV_FILE="/etc/deltajanebi-bot.env"
DATA_DIR="/var/lib/deltajanebi-bot"
SERVICE_FILE="/etc/systemd/system/deltajanebi-bot.service"
SERVICE_NAME="deltajanebi-bot"

[[ -f "$ENV_FILE" ]] || { echo "❌ تنظیمات ربات پیدا نشد. اول install-bot.sh را اجرا کن."; exit 1; }
[[ -d "$DATA_DIR" ]] || install -d -m 700 "$DATA_DIR"
[[ -d "$APP_DIR/.git" ]] || { echo "❌ سورس نصب‌شده ربات پیدا نشد. install-bot.sh را اجرا کن."; exit 1; }
[[ -d "$RUNTIME_DIR/.git" ]] || { echo "❌ runtime نصب‌شده SanaShop پیدا نشد. install-bot.sh را اجرا کن."; exit 1; }

OLD_APP_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
OLD_RUNTIME_SHA="$(git -C "$RUNTIME_DIR" rev-parse HEAD)"
SERVICE_WAS_ACTIVE=0
systemctl is-active --quiet "$SERVICE_NAME" && SERVICE_WAS_ACTIVE=1 || true
SERVICE_BACKUP="$(mktemp)"
if [[ -f "$SERVICE_FILE" ]]; then cp -a "$SERVICE_FILE" "$SERVICE_BACKUP"; else : >"$SERVICE_BACKUP"; fi
STOPPED=0
DONE=0

rollback(){
  local rc="${1:-1}"
  trap - ERR EXIT INT TERM
  set +e
  if [[ "$DONE" -eq 0 ]]; then
    echo
    echo "⚠️ آپدیت کامل نشد؛ در حال بازگردانی نسخه سالم قبلی..."
    git -C "$APP_DIR" reset --hard "$OLD_APP_SHA" >/dev/null 2>&1 || true
    git -C "$RUNTIME_DIR" reset --hard "$OLD_RUNTIME_SHA" >/dev/null 2>&1 || true
    if [[ -s "$SERVICE_BACKUP" ]]; then cp -a "$SERVICE_BACKUP" "$SERVICE_FILE"; fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    rm -f "$DATA_DIR/runtime.lock"
    if [[ "$SERVICE_WAS_ACTIVE" -eq 1 ]]; then
      systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
      if systemctl is-active --quiet "$SERVICE_NAME"; then
        echo "✅ نسخه قبلی ربات دوباره فعال شد؛ اتصال سایت‌ها و دیتابیس حفظ شدند."
      else
        echo "❌ بازگردانی انجام شد ولی سرویس بالا نیامد. لاگ: journalctl -u $SERVICE_NAME -n 120 --no-pager"
      fi
    fi
  fi
  rm -f "$SERVICE_BACKUP"
  exit "$rc"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT TERM
trap 'rc=$?; if [[ $rc -ne 0 && $DONE -eq 0 ]]; then rollback "$rc"; fi; rm -f "$SERVICE_BACKUP"' EXIT

echo "==> دریافت نسخه جدید Delta bot (بدون خاموش‌کردن سرویس فعلی)"
git -C "$APP_DIR" remote set-url origin https://github.com/deltashopsiavash/deltajanebi.git
git -C "$APP_DIR" fetch --prune origin main
git -C "$APP_DIR" reset --hard origin/main
NEW_APP_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"

echo "==> دریافت runtime پایدار SanaShop"
git -C "$RUNTIME_DIR" remote set-url origin https://github.com/deltashopsiavash/sanashop.git
git -C "$RUNTIME_DIR" fetch --prune origin main
git -C "$RUNTIME_DIR" reset --hard origin/main
NEW_RUNTIME_SHA="$(git -C "$RUNTIME_DIR" rev-parse HEAD)"

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then python3 -m venv "$APP_DIR/.venv"; fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install python-telegram-bot==22.3 httpx==0.28.1 >/dev/null

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
export DELTAJANEBI_RUNTIME_DIR="${DELTAJANEBI_RUNTIME_DIR:-$RUNTIME_DIR}"
export PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "==> Preflight: syntax/import/router/source controls"
"$APP_DIR/.venv/bin/python" -m py_compile \
  "$APP_DIR/external_bot_delta_v15.py" \
  "$APP_DIR/external_bot_delta_v16.py" \
  "$APP_DIR/delta_bot_native.py" \
  "$APP_DIR/delta_footer_restore.py" \
  "$APP_DIR/delta_source_restore.py"
(
  cd "$APP_DIR"
  "$APP_DIR/.venv/bin/python" - <<'PY'
import external_bot_delta_v16 as bot
import external_bot_delta_v15 as router
import delta_bot_native as delta
import delta_footer_restore as footer
import delta_source_restore as source
assert callable(bot.run)
assert callable(router.routed_site_panel)
assert callable(delta.callback)
assert callable(footer.install)
assert callable(source.install)
assert getattr(delta, "_delta_footer_restore_v17_installed", False) is True
assert getattr(delta, "_delta_source_restore_v18_installed", False) is True
print("Delta native multi-site bot source-control preflight: OK")
PY
)

# Only after all preflight checks pass do we interrupt the running bot.
echo "==> Preflight موفق؛ راه‌اندازی نسخه جدید"
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
STOPPED=1
rm -f "$DATA_DIR/runtime.lock"

cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=DeltaJanebi External Multi-site Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/external_bot_delta_v16.py
Restart=always
RestartSec=3
User=root
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 5
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "❌ نسخه جدید بالا نیامد؛ rollback خودکار اجرا می‌شود."
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
  false
fi

DONE=1
trap - ERR EXIT INT TERM
rm -f "$SERVICE_BACKUP"
echo "✅ ربات DeltaJanebi با موفقیت آپدیت شد."
echo "✅ preflight قبل از توقف انجام شد؛ در خطای آپدیت نسخه قبلی خودکار برمی‌گردد."
echo "✅ Router چندسایتی و کنترل‌های اختصاصی منبع Delta فعال هستند."
echo "✅ توکن، مالک، دیتابیس اتصال سایت‌ها و مدیران حفظ شدند."
echo "نسخه Delta: ${NEW_APP_SHA:0:7}"
echo "نسخه runtime: ${NEW_RUNTIME_SHA:0:7}"
grep '^ExecStart=' "$SERVICE_FILE"
systemctl status "$SERVICE_NAME" --no-pager
