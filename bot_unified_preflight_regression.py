#!/usr/bin/env python3
"""Regression for the production preflight with an UNEXPORTED owner and token."""
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory() as root:
        secret_file = Path(root) / "credentials.env"
        secret_file.write_text(
            "\n".join(
                f"{key}={shlex.quote(value)}" for key, value in {
                    "TELEGRAM_BOT_TOKEN": "123456789:CI_TOKEN_VALUE_NOT_USED_ON_NETWORK",
                    "TELEGRAM_OWNER_ID": "87654321",
                    "SANA_DB": f"{root}/test.sqlite3",
                    "SANA_LOCK": f"{root}/bot.lock",
                }.items()
            ) + "\n", encoding="utf-8"
        )
        secret_file.chmod(0o600)
        env = os.environ.copy()
        for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "BOT_DB_PATH", "BOT_LOCK_PATH"):
            env.pop(key, None)
        env["DELTAJANEBI_RUNTIME_DIR"] = "/tmp/sanashop-runtime"
        result = subprocess.run(
            [sys.executable, "scripts/unified-bot-preflight.py", str(secret_file), "/tmp/sanashop-runtime"],
            capture_output=True, text=True, env=env,
            check=False,
        )
        if result.returncode:
            # Never expose token data in a traceback.
            raise AssertionError("Isolated preflight failed: " + result.stderr[-2000:])
        assert "Unified SanaShop/Delta Python imports: OK" in result.stdout
    print("Preflight with completely unexported recovered owner/token: OK")


if __name__ == "__main__":
    main()
