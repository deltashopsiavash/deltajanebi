#!/usr/bin/env bash
# Make one Telegram bot serve SanaShop and DeltaJanebi, preserving the LIVE SanaShop
# SQLite DB, token, admin assignments and all SanaShop source files unchanged.
set -Eeuo pipefail
umask 077

SANA_DIR="/opt/sanashop-bot"
SANA_ENV="/etc/sanashop-bot.env"
SANA_SERVICE="sanashop-bot"
DELTA_DIR="/opt/deltajanebi-unified-bot"
DELTA_REPO="https://github.com/deltashopsiavash/deltajanebi.git"
UNIFIED_SERVICE="deltajanebi-unified-bot"
UNIFIED_ENV="/etc/deltajanebi-unified-bot.env"
UNIFIED_UNIT="/etc/systemd/system/deltajanebi-unified-bot.service"
BACKUP_ROOT="/var/backups/deltajanebi-unified"
OLD_DELTA_ENV="/etc/deltajanebi-bot.env"
OLD_DELTA_SERVICE="deltajanebi-bot"
ACTIVATION_STARTED=0
ACTIVATION_SUCCESS=0
BACKUP_DIR=""
SANA_WAS_ACTIVE=0
OLD_DELTA_WAS_ACTIVE=0
UNIFIED_WAS_ACTIVE=0
STOP_OLD_DELTA=0

error() {
  printf '\nERROR: %s\n' "$*" >&2
  if [[ "$ACTIVATION_STARTED" == 1 && "$ACTIVATION_SUCCESS" != 1 ]]; then
    rollback 1
  fi
  exit 1
}
log() { printf '\n==> %s\n' "$*"; }

rollback() {
  local status="${1:-$?}"
  trap - ERR
  if [[ "$ACTIVATION_STARTED" == 1 && "$ACTIVATION_SUCCESS" != 1 ]]; then
    echo "⚠️ Unified activation failed; restoring original SanaShop service/database." >&2
    systemctl stop "$UNIFIED_SERVICE" >/dev/null 2>&1 || true
    if [[ -s "$BACKUP_DIR/shared-db.sqlite3" ]]; then
      python3 - "$BACKUP_DIR/shared-db.sqlite3" "$SANA_DB" <<'PY' || true
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    src.close()
    dst.close()
PY
    fi
    if [[ -f "$BACKUP_DIR/unified.env" ]]; then
      cp -a "$BACKUP_DIR/unified.env" "$UNIFIED_ENV"
    else
      rm -f "$UNIFIED_ENV"
    fi
    if [[ -f "$BACKUP_DIR/unified.service" ]]; then
      cp -a "$BACKUP_DIR/unified.service" "$UNIFIED_UNIT"
    else
      rm -f "$UNIFIED_UNIT"
    fi
    systemctl daemon-reload >/dev/null 2>&1 || true
    if [[ "$UNIFIED_WAS_ACTIVE" == 1 ]]; then
      systemctl start "$UNIFIED_SERVICE" || true
    elif [[ "$SANA_WAS_ACTIVE" == 1 ]]; then
      systemctl start "$SANA_SERVICE" || true
    elif [[ "$OLD_DELTA_WAS_ACTIVE" == 1 ]]; then
      systemctl start "$OLD_DELTA_SERVICE" || true
    fi
    echo "Rollback completed. No SanaShop Git files were modified." >&2
  fi
  exit "$status"
}
trap rollback ERR

[[ $EUID -eq 0 ]] || error "Run as root via sudo."
[[ -x "$SANA_DIR/.venv/bin/python" ]] || error "SanaShop virtual environment missing: $SANA_DIR/.venv"
[[ -f "$SANA_DIR/external_bot_v16.py" ]] || error "SanaShop v16 code missing; no changes made."
command -v git >/dev/null || error "git missing."
command -v python3 >/dev/null || error "Python 3 missing."
command -v systemctl >/dev/null || error "systemd missing."

# The old script required a complete /etc/sanashop-bot.env, but older
# servers may store a shared bot identity in the running service or Delta env.
# Fetch Delta code first; no services, site files or DBs are touched yet.
log "Fetching Delta router only; SanaShop repository will NOT be touched"
if [[ -d "$DELTA_DIR/.git" ]]; then
  git -C "$DELTA_DIR" fetch --prune origin main
  git -C "$DELTA_DIR" reset --hard origin/main
elif [[ -e "$DELTA_DIR" ]]; then
  error "$DELTA_DIR is not a Git checkout. No service was stopped."
else
  git clone --depth 1 "$DELTA_REPO" "$DELTA_DIR"
fi
[[ -f "$DELTA_DIR/scripts/merge-unified-bot-db.py" ]] || error "Migration script missing."

log "Finding the existing Telegram bot token, owner and shared database"
RESOLVED_ENV="$(mktemp /tmp/deltajanebi-credentials.XXXXXXXX)"
chmod 600 "$RESOLVED_ENV"
trap 'rm -f "$RESOLVED_ENV"' EXIT
python3 "$DELTA_DIR/scripts/resolve-unified-bot-config.py" --output "$RESOLVED_ENV"
# This private file contains only Python shell-quoted assignments.
# shellcheck disable=SC1090
source "$RESOLVED_ENV"
rm -f "$RESOLVED_ENV"
trap - EXIT

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  [[ -r /dev/tty ]] || error "Existing Telegram bot token missing and no interactive TTY is available."
  printf '\nExisting Telegram bot token was not stored in known service configs.\n' >/dev/tty
  read -r -s -p "Paste the SAME current Telegram bot token (hidden): " TELEGRAM_BOT_TOKEN </dev/tty
  printf '\n' >/dev/tty
fi
if [[ ! "${TELEGRAM_OWNER_ID:-}" =~ ^[0-9]+$ ]]; then
  [[ -r /dev/tty ]] || error "Existing numeric Telegram owner ID missing and no interactive TTY is available."
  read -r -p "Enter the SAME numeric owner Telegram ID: " TELEGRAM_OWNER_ID </dev/tty
fi
[[ "$TELEGRAM_BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ && "$TELEGRAM_OWNER_ID" =~ ^[0-9]+$ ]] ||
  error "The existing bot token or numeric owner ID is invalid; no service or database was changed."

[[ -s "$SANA_DB" ]] || error "Existing shared DB not available: $SANA_DB. Refusing to create a blank DB."
if [[ ! -f "/etc/systemd/system/sanashop-bot.service" && ! -f "/etc/systemd/system/deltajanebi-bot.service" && ! -f "/etc/systemd/system/deltajanebi-unified-bot.service" ]]; then
  error "No existing SanaShop/Delta Telegram bot service was found. No changes made."
fi

# Never print the token, even if troubleshooting is enabled.
echo "Existing credentials loaded; shared database selected; values hidden."

# Only stop/migrate an old standalone Delta service if it uses the SAME token.
if [[ -r "$OLD_DELTA_ENV" ]]; then
  LEGACY_TOKEN="$( (unset TELEGRAM_BOT_TOKEN BOT_DB_PATH; source "$OLD_DELTA_ENV"; printf '%s' "${TELEGRAM_BOT_TOKEN:-}") )"
  if [[ "$LEGACY_TOKEN" == "$TELEGRAM_BOT_TOKEN" ]]; then
    STOP_OLD_DELTA=1
    OLD_DELTA_DB="$( (unset BOT_DB_PATH; source "$OLD_DELTA_ENV"; printf '%s' "${BOT_DB_PATH:-/var/lib/deltajanebi-bot/bot.sqlite3}") )"
  fi
fi
if [[ "$STOP_OLD_DELTA" == 0 ]]; then OLD_DELTA_DB=""; fi

log "Preflight imports using the existing SanaShop Python runtime"
cd "$DELTA_DIR"
"$SANA_DIR/.venv/bin/python" -m py_compile \
  external_bot_delta_v15.py external_bot_delta_v16.py delta_bot_native.py \
  delta_footer_restore.py delta_source_restore.py delta_catalog_ui_v20.py \
  delta_banner_controls_v31.py delta_help_pages_v33.py delta_site_title_v34.py
DELTAJANEBI_RUNTIME_DIR="$SANA_DIR" "$SANA_DIR/.venv/bin/python" - <<'PY'
import external_bot_delta_v16 as unified
import external_bot_delta_v15 as router
import delta_bot_native as delta
assert callable(unified.run)
assert callable(unified.cancel)
assert callable(router.routed_site_panel)
assert callable(delta.callback)
assert router.core.site_panel is router.routed_site_panel
print("Unified router and SanaShop runtime imports: OK")
PY

log "Making online backup of the existing SanaShop database"
install -d -m 700 "$BACKUP_ROOT"
BACKUP_DIR="$(mktemp -d "$BACKUP_ROOT/backup-XXXXXXXX")"
python3 - "$SANA_DB" "$BACKUP_DIR/shared-db.sqlite3" <<'PY'
import sqlite3, sys
source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
finally:
    source.close()
    target.close()
print("SanaShop database backup verified (credentials not printed)")
PY
[[ ! -f "$UNIFIED_ENV" ]] || cp -a "$UNIFIED_ENV" "$BACKUP_DIR/unified.env"
[[ ! -f "$UNIFIED_UNIT" ]] || cp -a "$UNIFIED_UNIT" "$BACKUP_DIR/unified.service"

systemctl is-active --quiet "$SANA_SERVICE" && SANA_WAS_ACTIVE=1 || true
systemctl is-active --quiet "$UNIFIED_SERVICE" && UNIFIED_WAS_ACTIVE=1 || true
if [[ "$STOP_OLD_DELTA" == 1 ]]; then
  systemctl is-active --quiet "$OLD_DELTA_SERVICE" && OLD_DELTA_WAS_ACTIVE=1 || true
fi

log "Stopping same-token pollers before switching to ONE shared router"
ACTIVATION_STARTED=1
systemctl stop "$SANA_SERVICE" || true
systemctl stop "$UNIFIED_SERVICE" || true
if [[ "$STOP_OLD_DELTA" == 1 ]]; then
  systemctl stop "$OLD_DELTA_SERVICE" || true
fi
systemctl is-active --quiet "$SANA_SERVICE" && error "SanaShop poller remained active" || true
systemctl is-active --quiet "$UNIFIED_SERVICE" && error "Old unified poller remained active" || true
if [[ "$STOP_OLD_DELTA" == 1 ]]; then
  systemctl is-active --quiet "$OLD_DELTA_SERVICE" && error "Old Delta poller remained active" || true
fi

log "Reusing SanaShop connections and importing Delta connections if present"
if [[ -n "$OLD_DELTA_DB" && -f "$OLD_DELTA_DB" ]]; then
  python3 scripts/merge-unified-bot-db.py "$OLD_DELTA_DB" "$SANA_DB"
else
  echo "No separate legacy Delta database to import."
fi

log "Creating ONE unified service; original SanaShop files and database path remain intact"
cat >"$UNIFIED_ENV" <<EOF
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_OWNER_ID=$TELEGRAM_OWNER_ID
BOT_DB_PATH=$SANA_DB
BOT_LOCK_PATH=$SANA_LOCK
DELTAJANEBI_RUNTIME_DIR=$SANA_DIR
EOF
chmod 600 "$UNIFIED_ENV"

cat >"$UNIFIED_UNIT" <<EOF
[Unit]
Description=Shared SanaShop + DeltaJanebi site-aware Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DELTA_DIR
EnvironmentFile=$UNIFIED_ENV
ExecStart=$SANA_DIR/.venv/bin/python $DELTA_DIR/external_bot_delta_v16.py
Restart=always
RestartSec=3
User=root
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$UNIFIED_SERVICE" >/dev/null
systemctl restart "$UNIFIED_SERVICE"
sleep 4
systemctl is-active --quiet "$UNIFIED_SERVICE" || {
  journalctl -u "$UNIFIED_SERVICE" -n 70 --no-pager >&2 || true
  error "Unified bot service failed to start."
}
PID="$(systemctl show -p MainPID --value "$UNIFIED_SERVICE")"
[[ "$PID" =~ ^[1-9][0-9]*$ ]] || error "Unified bot main PID unavailable."
[[ -r "/proc/$PID/cmdline" ]] || error "Unified bot process missing."
if ! tr '\0' ' ' <"/proc/$PID/cmdline" | grep -Fq "$DELTA_DIR/external_bot_delta_v16.py"; then
  error "Unified bot is running the wrong entrypoint."
fi

# Disable old units only after the new unified poller has passed verification.
systemctl disable "$SANA_SERVICE" >/dev/null 2>&1 || true
if [[ "$STOP_OLD_DELTA" == 1 ]]; then
  systemctl disable "$OLD_DELTA_SERVICE" >/dev/null 2>&1 || true
fi
ACTIVATION_SUCCESS=1
trap - ERR
echo
echo "✅ One Telegram bot now serves SanaShop and DeltaJanebi with separate site panels."
echo "✅ SanaShop repo, existing site records, API keys and admins were NOT changed."
echo "✅ Old services are stopped; shared bot DB and lock remain in SanaShop's original location."
echo "🏪 Telegram: /start -> sites -> open site. Old Delta/Sana buttons should route to their own site."
echo "🔎 Status: systemctl status deltajanebi-unified-bot --no-pager"
echo "📄 Logs: journalctl -u deltajanebi-unified-bot -n 50 --no-pager"
echo "⚠️ After updating SanaShop bot on this host, re-run this unified activation command to keep a single poller."
