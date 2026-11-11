#!/usr/bin/env python3
"""Run Delta/Sana unified bot import preflight using private sourced credentials.

The resolver's file is mode 0600 and contains shlex-compatible shell assignments.
By injecting the exact same recovered values into os.environ BEFORE importing
SanaShop's external_bot, this remains reliable even if the parent shell did not
export values. No credentials are printed or passed as process argv.
"""
import os
from pathlib import Path
import re
import shlex
import sys


def read_credentials(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, raw = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z_][A-Z_0-9]*", key):
            raise RuntimeError("Unexpected private credential file format")
        values = shlex.split(raw)
        if len(values) != 1:
            raise RuntimeError("Unexpected private credential value format")
        result[key] = values[0]
    return result


def configure_runtime(private_file, sana_runtime):
    values = read_credentials(private_file)
    token = values.get("TELEGRAM_BOT_TOKEN") or ""
    owner = values.get("TELEGRAM_OWNER_ID") or ""
    db = values.get("SANA_DB") or ""
    lock = values.get("SANA_LOCK") or ""
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise RuntimeError("No valid preflight bot token was recovered")
    if not re.fullmatch(r"[0-9]+", owner):
        raise RuntimeError("No valid preflight numeric owner was recovered")
    if not db or not lock:
        raise RuntimeError("Shared database path or lock path missing")
    os.environ.update({
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_OWNER_ID": owner,
        "BOT_DB_PATH": db,
        "BOT_LOCK_PATH": lock,
        "DELTAJANEBI_RUNTIME_DIR": sana_runtime,
    })
    if not os.environ.get("TELEGRAM_OWNER_ID"):
        raise RuntimeError("Owner ID missing from Python environment")
    return owner, db


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: unified-bot-preflight.py PRIVATE_RESOLVED_ENV SANA_RUNTIME_DIR")
    owner, db = configure_runtime(sys.argv[1], sys.argv[2])
    # This import chain mirrors the actual systemd entrypoint, not a mock.
    import external_bot_delta_v16 as unified
    import external_bot_delta_v15 as router
    import delta_bot_native as delta

    assert router.core.OWNER_ID == int(owner), "SanaShop owner mismatch"
    assert str(router.core.DB_PATH) == db, "SanaShop database mismatch"
    assert callable(unified.run) and callable(unified.cancel)
    assert callable(router.routed_site_panel) and callable(delta.callback)
    assert router.core.site_panel is router.routed_site_panel
    print("Unified SanaShop/Delta Python imports: OK (credentials hidden)")


if __name__ == "__main__":
    main()
