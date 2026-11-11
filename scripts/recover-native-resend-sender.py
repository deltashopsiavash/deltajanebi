#!/usr/bin/env python3
"""Test the legacy Resend sender, then persist it ONLY if delivery is accepted."""
import grp
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
from datetime import datetime

APP = Path("/opt/deltajanebi")
CONFIG = Path("/etc/deltajanebi/native.env")
ENV_COPY = APP / ".env"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run as root/sudo.")
    if len(sys.argv) != 2 or not EMAIL.fullmatch(sys.argv[1]):
        raise SystemExit("Usage: recover-native-resend-sender.py recipient@example.com")
    if not CONFIG.is_file() or not (APP / ".venv/bin/python").is_file():
        raise SystemExit("Native DeltaJanebi installation is missing.")

    funcs = runpy.run_path(str(APP / "scripts/configure-native-email.py"))
    original = CONFIG.read_text(encoding="utf8")
    config = funcs["config_values"](original)
    domain = str(config.get("DOMAIN") or "").lower().strip()
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", domain):
        raise SystemExit("Invalid configured site domain. No sender setting changed.")
    current_sender = (config.get("DEFAULT_FROM_EMAIL") or "").strip()
    candidate = "support@mail." + domain

    print("Current sender:", current_sender)
    print("Legacy installer candidate:", candidate)
    if current_sender.lower() == candidate.lower():
        raise SystemExit(
            "The legacy sender is already configured. Check Resend Domains and API key restrictions."
        )
    if not (config.get("RESEND_API_KEY") or config.get("SMTP_PASSWORD")):
        raise SystemExit("No saved Resend key found. Configure Resend first.")

    python = str(APP / ".venv/bin/python")
    command = [
        "runuser", "-u", "deltajanebi", "--", python,
        str(APP / "scripts/native-env-exec.py"), str(CONFIG),
        python, str(APP / "scripts/check-native-email.py"),
        sys.argv[1], "--sender", candidate,
    ]
    print("==> Testing old sender without modifying site settings")
    result = subprocess.run(command, cwd=APP, check=False)
    if result.returncode:
        print(
            "❌ Legacy sender was also rejected. Site settings were NOT changed. "
            "Check the exact Verified domain at resend.com/domains, "
            "and check API Key domain restrictions."
        )
        raise SystemExit(result.returncode)

    print("==> Resend accepted the old sender. Saving the corrected sender address.")
    backup = Path("/var/backups/deltajanebi")
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_file = backup / ("native-before-sender-fix-%s-%s.env" % (
        datetime.now().strftime("%Y%m%d-%H%M%S"), os.getpid()
    ))
    backup_file.write_text(original, encoding="utf8")
    os.chmod(backup_file, 0o600)

    gid = grp.getgrnam("deltajanebi").gr_gid
    revised = funcs["update_config"](original, {"DEFAULT_FROM_EMAIL": candidate})
    funcs["write_private"](CONFIG, revised, gid)
    shutil.copyfile(CONFIG, ENV_COPY)
    os.chown(ENV_COPY, 0, gid)
    os.chmod(ENV_COPY, 0o640)
    subprocess.run(["systemctl", "restart", "deltajanebi-web"], check=True)
    subprocess.run(["systemctl", "is-active", "--quiet", "deltajanebi-web"], check=True)
    print("✅ Resend accepted the sender. DeltaJanebi now uses:", candidate)
    print("✅ API key, PostgreSQL, user accounts, media, and SanaShop were not changed.")
    print("Check your test inbox, then retry the verification code on the site.")


if __name__ == "__main__":
    main()
