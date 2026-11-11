#!/usr/bin/env python3
"""Regression: shared-bot activation must actually expand secrets from env file."""
from pathlib import Path

script = Path("scripts/activate-unified-bot.sh").read_text(encoding="utf-8")
assert "\\" + "${" not in script, "Escaped shell parameter expansion would disable token/owner checks"
for variable in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "BOT_DB_PATH", "BOT_LOCK_PATH"):
    assert "${" + variable + ":-" in script, f"Shell variable {variable} must expand"
assert 'source "$SANA_ENV"' in script
assert 'external_bot_delta_v16.py' in script
assert 'systemctl stop "$SANA_SERVICE"' in script
assert 'ACTIVATION_STARTED=1' in script
assert 'ACTIVATION_SUCCESS=1' in script
print("Shared bot activation script environment contract: OK")
