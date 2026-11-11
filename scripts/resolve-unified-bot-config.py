#!/usr/bin/env python3
"""Resolve existing shared Telegram bot identity without printing credentials.

Reads only root-visible systemd process environment and known bot env files.
Emits shell-quoted assignments to a private file for activation script.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess

SERVICES = ("deltajanebi-unified-bot", "sanashop-bot", "deltajanebi-bot")
FIELDS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "BOT_DB_PATH", "BOT_LOCK_PATH")


def parse_env(contents):
    values = {}
    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if key not in FIELDS:
            continue
        val = raw.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        values[key] = val
    return values


def read_env_file(path):
    try:
        return parse_env(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return {}


def process_environment(pid):
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
        return {
            key: value for part in data.split(b"\0") if b"=" in part
            for key, value in [part.decode("utf-8", "replace").split("=", 1)]
            if key in FIELDS
        }
    except (OSError, ValueError):
        return {}


def active_processes():
    found = []
    for service in SERVICES:
        try:
            result = subprocess.run(
                ["systemctl", "show", "-p", "MainPID", "--value", service],
                text=True, capture_output=True, timeout=6, check=False
            )
            pid = int(result.stdout.strip() or 0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pid = 0
        if pid > 0:
            found.append((service, process_environment(pid)))
    return found


def choose_identity(active, files, inherited=None):
    """Choose one live token; never merge owner IDs from different tokens."""
    inherited = inherited or {}
    active_tokens = {data.get("TELEGRAM_BOT_TOKEN") for _, data in active if data.get("TELEGRAM_BOT_TOKEN")}
    if len(active_tokens) > 1:
        raise ValueError("Different Telegram bot tokens are ACTIVE on this server; no service can be safely replaced.")
    active_token = next(iter(active_tokens), "")
    candidates = (
        [(name, data) for name, data in active]
        + [(name, data) for name, data in files]
        + [("provided environment", inherited)]
    )
    token = active_token
    if not token:
        for name, data in candidates:
            if data.get("TELEGRAM_BOT_TOKEN"):
                token = data["TELEGRAM_BOT_TOKEN"]
                break

    owner = ""
    source = ""
    for name, data in candidates:
        if data.get("TELEGRAM_BOT_TOKEN") != token or not token:
            continue
        possible = str(data.get("TELEGRAM_OWNER_ID") or "").strip()
        if re.fullmatch(r"[0-9]+", possible):
            owner, source = possible, name
            break
    if not source:
        source = next((name for name, data in candidates if data.get("TELEGRAM_BOT_TOKEN") == token and token), "unavailable")
    return token, owner, source


def choose_database(files):
    by_name = {name: info for name, info in files}
    for label in ("sanashop-bot.env", "deltajanebi-unified-bot.env", "deltajanebi-bot.env"):
        conf = by_name.get(label, {})
        defaults = {
            "sanashop-bot.env": ("/var/lib/sanashop-bot/bot.sqlite3", "/var/lib/sanashop-bot/runtime.lock"),
            "deltajanebi-unified-bot.env": ("/var/lib/sanashop-bot/bot.sqlite3", "/var/lib/sanashop-bot/runtime.lock"),
            "deltajanebi-bot.env": ("/var/lib/deltajanebi-bot/bot.sqlite3", "/var/lib/deltajanebi-bot/runtime.lock"),
        }
        default_db, default_lock = defaults[label]
        db_path = conf.get("BOT_DB_PATH") or default_db
        lock_path = conf.get("BOT_LOCK_PATH") or default_lock
        if Path(db_path).is_file() and Path(db_path).stat().st_size > 0:
            return db_path, lock_path
    raise ValueError("Existing SanaShop/Delta bot database not found. Refusing to create a blank replacement.")


def resolve(file_paths, active=None, inherited=None):
    files = [(Path(path).name, read_env_file(path)) for path in file_paths]
    token, owner, source = choose_identity(active if active is not None else active_processes(), files, inherited)
    database, lock = choose_database(files)
    return {
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_OWNER_ID": owner,
        "SANA_DB": database,
        "SANA_LOCK": lock,
        "CREDENTIAL_SOURCE": source,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="root-only output file; never display contents")
    parser.add_argument("--sana-env", default="/etc/sanashop-bot.env")
    parser.add_argument("--unified-env", default="/etc/deltajanebi-unified-bot.env")
    parser.add_argument("--delta-env", default="/etc/deltajanebi-bot.env")
    args = parser.parse_args()
    try:
        data = resolve((args.sana_env, args.unified_env, args.delta_env),
                       inherited={key: os.getenv(key, "") for key in FIELDS})
    except ValueError as error:
        parser.exit(2, f"ERROR: {error}\n")
    output = Path(args.output)
    if not output.is_file() or output.stat().st_mode & 0o077:
        parser.exit(2, "ERROR: output must be an existing root-only private file.\n")
    output.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in data.items()), encoding="utf-8")
    os.chmod(output, 0o600)
    print(f"Existing bot configuration located ({data['CREDENTIAL_SOURCE']}). Credentials hidden.")


if __name__ == "__main__":
    main()
