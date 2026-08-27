#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID} -eq 0 ]] || { echo "این دستور باید با sudo/root اجرا شود."; exit 1; }

APP_DIR="/opt/deltajanebi-bot"
RUNTIME_DIR="/opt/deltajanebi-bot-runtime"
REPO_URL="https://github.com/deltashopsiavash/deltajanebi.git"
RUNTIME_REPO="https://github.com/deltashopsiavash/sanashop.git"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_OWNER_ID="${TELEGRAM_OWNER_ID:-}"

read_tty(){ local prompt="$1" var="$2" secret="${3:-0}" value="${!2:-}"; if [[ -z "$value" ]]; then [[ -r /dev/tty ]] || { echo "ترمینال تعاملی در دسترس نیست."; exit 1; }; if [[ "$secret" == 1 ]]; then read -r -s -p "$prompt" value </dev/tty; echo >/dev/tty; else read -r -p "$prompt" value </dev/tty; fi; fi; printf -v "$var" '%s' "$value"; }

read_tty "Telegram bot token: " TELEGRAM_BOT_TOKEN 1
read_tty "Owner numeric Telegram ID: " TELEGRAM_OWNER_ID
[[ -n "$TELEGRAM_BOT_TOKEN" && "$TELEGRAM_OWNER_ID" =~ ^[0-9]+$ ]] || { echo "توکن و آیدی عددی مالک الزامی هستند."; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git python3 python3-venv procps

if ! curl -fsS --max-time 15 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | grep -q '"ok":true'; then
  echo "❌ توکن ربات معتبر نیست یا سرور خارج به Telegram API دسترسی ندارد."
  exit 1
fi

systemctl disable --now deltajanebi-bot 2>/dev/null || true
pkill -TERM -f "$APP_DIR/external_bot_delta_v[0-9]+\.py" 2>/dev/null || true
sleep 2
pkill -KILL -f "$APP_DIR/external_bot_delta_v[0-9]+\.py" 2>/dev/null || true

rm -rf "$APP_DIR" "$RUNTIME_DIR"
git clone --depth 1 "$REPO_URL" "$APP_DIR"
git clone --depth 1 "$RUNTIME_REPO" "$RUNTIME_DIR"

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install python-telegram-bot==22.3 httpx==0.28.1

install -d -m 700 /var/lib/deltajanebi-bot
rm -f /var/lib/deltajanebi-bot/runtime.lock
cat >/etc/deltajanebi-bot.env <<EOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_OWNER_ID=$TELEGRAM_OWNER_ID
BOT_DB_PATH=/var/lib/deltajanebi-bot/bot.sqlite3
BOT_LOCK_PATH=/var/lib/deltajanebi-bot/runtime.lock
DELTAJANEBI_RUNTIME_DIR=$RUNTIME_DIR
EOF
chmod 600 /etc/deltajanebi-bot.env

set -a
# shellcheck disable=SC1091
. /etc/deltajanebi-bot.env
set +a
export PYTHONPATH="$RUNTIME_DIR${PYTHONPATH:+:$PYTHONPATH}"
.venv/bin/python -m py_compile external_bot_delta_v15.py external_bot_delta_v16.py delta_bot_native.py delta_footer_restore.py delta_source_restore.py
.venv/bin/python - <<'PY'
import external_bot_delta_v16 as bot
import external_bot_delta_v15 as router
import delta_bot_native as delta
import delta_footer_restore as footer
import delta_source_restore as source
assert callable(bot.run) and callable(router.routed_site_panel) and callable(delta.callback)
assert callable(footer.install) and callable(source.install)
assert getattr(delta, "_delta_footer_restore_v17_installed", False) is True
assert getattr(delta, "_delta_source_restore_v18_installed", False) is True
print("Delta native multi-site bot v18 source controls: OK")
PY

cat >/etc/systemd/system/deltajanebi-bot.service <<EOF
[Unit]
Description=DeltaJanebi External Multi-site Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=/etc/deltajanebi-bot.env
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
  echo "❌ سرویس ربات بالا نیامد."
  journalctl -u deltajanebi-bot -n 120 --no-pager
  exit 1
fi

echo
echo "✅ ربات خارجی DeltaJanebi نصب و Telegram تست شد."
echo "✅ Router چندسایتی و کنترل‌های اختصاصی منبع Delta فعال هستند."
echo "✅ اطلاعات اتصال سایت‌ها و مدیران در /var/lib/deltajanebi-bot حفظ می‌شود."
echo "داخل تلگرام: /start → 🔗 اتصال سایت → آدرس سایت → API Key همان سایت"
echo "وضعیت: systemctl status deltajanebi-bot --no-pager"
echo "لاگ:    journalctl -u deltajanebi-bot -f"
