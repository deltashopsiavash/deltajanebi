#!/usr/bin/env python3
"""Regression: unified-bot activation must safely load real existing credentials."""
from pathlib import Path

activation = Path("scripts/activate-unified-bot.sh").read_text(encoding="utf-8")
discovery = Path("scripts/resolve-unified-bot-config.py").read_text(encoding="utf-8")
assert "\\" + "${" not in activation, "Escaped shell parameters stop expansion"
for token in ("${TELEGRAM_BOT_TOKEN:-}", "${TELEGRAM_OWNER_ID:-}"):
    assert token in activation, token
assert 'source "$RESOLVED_ENV"' in activation
assert 'scripts/unified-bot-preflight.py "$RESOLVED_ENV" "$SANA_DIR"' in activation
assert 'rm -f "$RESOLVED_ENV"' in activation
assert activation.index('scripts/unified-bot-preflight.py "$RESOLVED_ENV" "$SANA_DIR"') < activation.index('rm -f "$RESOLVED_ENV"\ntrap - EXIT'), "Private file must survive the preflight"
assert 'BOT_DB_PATH="$SANA_DB"' in activation
assert 'BOT_LOCK_PATH="$SANA_LOCK"' in activation
assert "export TELEGRAM_BOT_TOKEN TELEGRAM_OWNER_ID BOT_DB_PATH BOT_LOCK_PATH" in activation
assert activation.index("export TELEGRAM_BOT_TOKEN TELEGRAM_OWNER_ID BOT_DB_PATH BOT_LOCK_PATH") < activation.index("log \"Preflight imports"), "Python preflight must receive exported credentials"
assert "resolve-unified-bot-config.py" in activation
assert '[[ -s "$SANA_DB" ]]' in activation
assert 'BOT_DB_PATH' in discovery and 'BOT_LOCK_PATH' in discovery
assert 'process_environment' in discovery and 'read_env_file' in discovery
assert 'systemctl stop "$SANA_SERVICE"' in activation
assert 'ACTIVATION_STARTED=1' in activation
assert 'ACTIVATION_SUCCESS=1' in activation
assert 'external_bot_delta_v16.py' in activation
assert 'scripts/merge-unified-bot-db.py' in activation
assert 'rm -rf "$SANA_DIR"' not in activation
print("Shared bot activation environment and safety contract: OK")
