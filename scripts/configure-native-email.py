#!/usr/bin/env python3
"""Interactive, secure Resend configuration for native DeltaJanebi deployment."""
import getpass
import grp
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

APP = Path("/opt/deltajanebi")
CONFIG = Path("/etc/deltajanebi/native.env")
ENV_COPY = APP / ".env"
EMAIL_PATTERN = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[a-zA-Z]{2,}")


def config_values(content):
    import shlex
    result = {}
    for line in content.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values = shlex.split(value)
            if len(values) == 1:
                result[key.strip()] = values[0]
    return result


def quoted(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "") + '"'


def update_config(original, updates):
    lines = []
    seen = set()
    for line in original.splitlines():
        key = line.partition("=")[0].strip()
        if key in updates:
            if key not in seen:
                lines.append(key + "=" + quoted(updates[key]))
                seen.add(key)
        else:
            lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            lines.append(key + "=" + quoted(value))
    return "\n".join(lines) + "\n"


def prompt(message):
    # STDIN belongs to the piped Bash installer; read from /dev/tty instead.
    with open("/dev/tty", "r+", encoding="utf8") as tty:
        tty.write(message)
        tty.flush()
        return tty.readline().strip()


def write_private(path, contents, group):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf8", dir=path.parent, prefix=".resend-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(contents)
        os.chown(temporary, 0, group)
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Root/sudo is required.")
    if not CONFIG.exists() or not (APP / ".venv/bin/python").exists():
        raise SystemExit("Native DeltaJanebi installation not found; Docker is not supported.")
    if not Path("/dev/tty").exists():
        raise SystemExit("An SSH terminal is required to enter private API credentials.")

    original = CONFIG.read_text(encoding="utf8")
    old = config_values(original)
    sender_default = old.get("DEFAULT_FROM_EMAIL", "")
    saved_key = old.get("RESEND_API_KEY") or old.get("SMTP_PASSWORD") or ""

    print("\n📨 Configure registration emails using Resend HTTPS (port 443).")
    print("Sender must be on a Resend-verified domain; Gmail is valid as recipient, not sender.")
    sender = prompt("Verified sender email [%s]: " % sender_default) or sender_default
    if not EMAIL_PATTERN.fullmatch(sender):
        raise SystemExit("Invalid sender email.")

    if saved_key:
        api_key = getpass.getpass("Resend API key (hidden; Enter to reuse existing): ").strip() or saved_key
    else:
        api_key = getpass.getpass("Resend API key (hidden): ").strip()
    if not api_key.startswith("re_") or len(api_key) < 12:
        raise SystemExit("Resend API key must start with re_; nothing was changed.")

    recipient = prompt("Test recipient email (e.g. Gmail): ")
    if not EMAIL_PATTERN.fullmatch(recipient):
        raise SystemExit("Invalid test recipient email; nothing was changed.")

    changes = {
        "DEFAULT_FROM_EMAIL": sender,
        "RESEND_API_KEY": api_key,
        "SMTP_PASSWORD": api_key,
        "SMTP_HOST": "smtp.resend.com",
        "SMTP_USER": "resend",
        "DELTA_EMAIL_TRANSPORT": "resend",
    }
    group = grp.getgrnam("deltajanebi").gr_gid
    backup_dir = Path("/var/backups/deltajanebi")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / ("native-before-email-%s-%s.env" % (stamp, os.getpid()))
    backup_path.write_text(original, encoding="utf8")
    os.chmod(backup_path, 0o600)

    write_private(CONFIG, update_config(original, changes), group)
    shutil.copyfile(CONFIG, ENV_COPY)
    os.chown(ENV_COPY, 0, group)
    os.chmod(ENV_COPY, 0o640)

    print("Private email configuration updated (key hidden). Restarting Django web only.")
    subprocess.run(["systemctl", "restart", "deltajanebi-web"], check=True)
    python = str(APP / ".venv/bin/python")
    result = subprocess.run(
        ["runuser", "-u", "deltajanebi", "--", python,
         str(APP / "scripts/native-env-exec.py"), str(CONFIG),
         python, str(APP / "scripts/check-native-email.py"), recipient],
        cwd=APP, check=False,
    )
    if result.returncode:
        raise SystemExit(
            "❌ Test mail not accepted by Resend. Re-run this command to correct the key "
            "or sender. Check domain verification, API permissions and HTTPS/443 connectivity. "
            "Users/products were not altered."
        )
    print("✅ Resend is configured. Try a new signup verification code or resend in the browser.")


if __name__ == "__main__":
    main()
