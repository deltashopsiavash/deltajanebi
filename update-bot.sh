#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "این دستور باید با sudo/root اجرا شود."; exit 1; }
APP_DIR="/opt/deltajanebi-bot"
RUNTIME_DIR="/opt/deltajanebi-bot-runtime"
ENV_FILE="/etc/deltajanebi-bot.env"
DATA_DIR="/var/lib/deltajanebi-bot"

[[ -f "$ENV_FILE" ]] || { echo "❌ تنظیمات ربات پیدا نشد. اول install-bot.sh را اجرا کن."; exit 1; }
[[ -d "$DATA_DIR" ]] || install -d -m 700 "$DATA_DIR"

systemctl stop deltajanebi-bot 2>/dev/null || true
rm -f "$DATA_DIR/runtime.lock"

echo "==> آپدیت سورس Delta bot"
if [[ ! -d "$APP_DIR/.git" ]]; then
  rm -rf "$APP_DIR"
  git clone --depth 1 https://github.com/deltashopsiavash/deltajanebi.git "$APP_DIR"
else
  git -C "$APP_DIR" remote set-url origin https://github.com/deltashopsiavash/deltajanebi.git
  git -C "$APP_DIR" fetch --prune origin main
  git -C "$APP_DIR" reset --hard origin/main
fi

echo "==> آپدیت runtime پایدار SanaShop"
if [[ ! -d "$RUNTIME_DIR/.git" ]]; then
  rm -rf "$RUNTIME_DIR"
  git clone --depth 1 https://github.com/deltashopsiavash/sanashop.git "$RUNTIME_DIR"
else
  git -C "$RUNTIME_DIR" remote set-url origin https://github.com/deltashopsiavash/sanashop.git
  git -C "$RUNTIME_DIR" fetch --prune origin main
  git -C "$RUNTIME_DIR" reset --hard origin/main
fi

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then python3 -m venv "$APP_DIR/.venv"; fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/pip" install python-telegram-bot==22.3 httpx==0.28.1 >/dev/null

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
export DELTAJANEBI_RUNTIME_DIR="${DELTAJANEBI_RUNTIME_DIR:-$RUNTIME_DIR}"
export PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}"
"$APP_DIR/.venv/bin/python" -m py_compile "$APP_DIR/external_bot_delta_v15.py" "$APP_DIR/external_bot_delta_v16.py" "$APP_DIR/delta_bot_native.py"
"$APP_DIR/.venv/bin/python" - <<'PY'
import external_bot_delta_v16 as bot
import external_bot_delta_v15 as router
import delta_bot_native as delta
assert callable(bot.run) and callable(router.routed_site_panel) and callable(delta.callback)
print("Delta native multi-site bot v16: OK")
PY

cat >/etc/systemd/system/deltajanebi-bot.service <<EOF
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
systemctl enable --now deltajanebi-bot
sleep 5
if ! systemctl is-active --quiet deltajanebi-bot; then
  echo "❌ ربات بعد از آپدیت بالا نیامد."
  journalctl -u deltajanebi-bot -n 140 --no-pager
  exit 1
fi

echo "✅ ربات DeltaJanebi v16 آپدیت شد."
echo "✅ Router چندسایتی فعال است؛ Delta و SanaShop پنل و callback مستقل دارند."
echo "✅ توکن، مالک، دیتابیس اتصال سایت‌ها و مدیران حفظ شدند."
grep '^ExecStart=' /etc/systemd/system/deltajanebi-bot.service
systemctl status deltajanebi-bot --no-pager
