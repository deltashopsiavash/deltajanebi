#!/usr/bin/env python3
"""Credential discovery is safe with partial env files and shared bot setups."""
import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile


def load():
    spec = importlib.util.spec_from_file_location("config", "scripts/resolve-unified-bot-config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load()
    token1 = "123456789:" + "A" * 35
    token2 = "987654321:" + "B" * 35
    with tempfile.TemporaryDirectory() as root:
        base = Path(root)
        files = [base / n for n in ("sanashop-bot.env", "deltajanebi-unified-bot.env", "deltajanebi-bot.env")]
        for file in files:
            file.touch()
        db = base / "shared.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE sites(id INTEGER PRIMARY KEY, base_url TEXT)")
            conn.commit()
        files[0].write_text(f"BOT_DB_PATH={db}\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_OWNER_ID=\n")
        files[2].write_text(f"TELEGRAM_BOT_TOKEN='{token1}'\nTELEGRAM_OWNER_ID=123456789\n")
        result = module.resolve(files, active=[])
        assert result["TELEGRAM_BOT_TOKEN"] == token1
        assert result["TELEGRAM_OWNER_ID"] == "123456789"
        assert result["SANA_DB"] == str(db)
        # Never borrow an owner from a different token.
        files[0].write_text(f"BOT_DB_PATH={db}\nTELEGRAM_BOT_TOKEN={token2}\n")
        result = module.resolve(files, active=[])
        assert result["TELEGRAM_BOT_TOKEN"] == token2
        assert result["TELEGRAM_OWNER_ID"] == ""
        # An active process is stronger evidence than a stale config.
        result = module.resolve(files, active=[("sanashop-bot", {"TELEGRAM_BOT_TOKEN": token1, "TELEGRAM_OWNER_ID": "777"})])
        assert result["TELEGRAM_BOT_TOKEN"] == token1
        assert result["TELEGRAM_OWNER_ID"] == "777"
        # Never take over when two different Telegram bots are running.
        try:
            module.resolve(files, active=[
                ("sanashop-bot", {"TELEGRAM_BOT_TOKEN": token1}),
                ("deltajanebi-bot", {"TELEGRAM_BOT_TOKEN": token2}),
            ])
        except ValueError as e:
            assert "Different Telegram bot tokens" in str(e)
        else:
            raise AssertionError("Different active bot tokens must be rejected")
    print("Unified bot credential resolution and safety: OK")


if __name__ == "__main__":
    main()
