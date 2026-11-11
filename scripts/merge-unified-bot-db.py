#!/usr/bin/env python3
"""Import connections from an old Delta bot DB to the shared SanaShop DB.

The source is read-only, target keys take precedence, IDs are remapped, and
the target transaction rolls back on errors. Never print API key values.
"""
import os
import sqlite3
import sys
from urllib.parse import urlsplit


def columns(conn, table):
    if table not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
        return set()
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def site_identity(value):
    raw = str(value or "").strip()
    url = urlsplit(raw if "://" in raw else "https://" + raw)
    return (url.hostname or "").lower(), (url.path or "").rstrip("/")


def merge(source_path, target_path):
    source_path = os.path.realpath(source_path)
    target_path = os.path.realpath(target_path)
    results = {"added": 0, "matched": 0, "admins": 0}
    if source_path == target_path or not os.path.isfile(source_path):
        return results
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    try:
        if not {"id", "name", "base_url", "api_key"}.issubset(columns(source, "sites")):
            raise RuntimeError("Legacy bot DB has no valid sites table")
        target.execute("BEGIN IMMEDIATE")
        target.execute("CREATE TABLE IF NOT EXISTS sites(id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL UNIQUE, api_key TEXT NOT NULL)")
        target.execute("CREATE TABLE IF NOT EXISTS admins(telegram_id INTEGER PRIMARY KEY, added_by INTEGER NOT NULL)")
        target.execute("CREATE TABLE IF NOT EXISTS site_admins(site_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL UNIQUE, UNIQUE(site_id, telegram_id))")
        for name, sql in (
            ("platform", "TEXT NOT NULL DEFAULT 'unknown'"),
            ("api_version", "INTEGER NOT NULL DEFAULT 0"),
            ("capabilities_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in columns(target, "sites"):
                target.execute(f"ALTER TABLE sites ADD COLUMN {name} {sql}")

        present = {site_identity(s["base_url"]): dict(s) for s in target.execute("SELECT * FROM sites")}
        id_map = {}
        for old in source.execute("SELECT * FROM sites ORDER BY id"):
            record = dict(old)
            identity = site_identity(record["base_url"])
            if not identity[0]:
                continue
            existing = present.get(identity)
            platform = str(record.get("platform") or "unknown").lower()
            if platform not in {"sanashop", "deltajanebi"}:
                platform = "unknown"
            if existing:
                site_id = int(existing["id"])
                results["matched"] += 1
                # Existing URL/credentials/name are authoritative, never overwrite.
                # The unified router will redetect platform through authenticated ping.
            else:
                cur = target.execute(
                    "INSERT INTO sites(name,base_url,api_key,platform,api_version,capabilities_json) VALUES(?,?,?,?,?,?)",
                    (record["name"], record["base_url"], record["api_key"], platform,
                     int(record.get("api_version") or 0), str(record.get("capabilities_json") or "[]")),
                )
                site_id = int(cur.lastrowid)
                present[identity] = {"id": site_id, "platform": platform}
                results["added"] += 1
            id_map[int(record["id"])] = site_id

        if {"site_id", "telegram_id"}.issubset(columns(source, "site_admins")):
            for record in source.execute("SELECT site_id, telegram_id FROM site_admins"):
                new_id = id_map.get(int(record["site_id"]))
                if new_id:
                    target.execute(
                        "INSERT OR IGNORE INTO site_admins(site_id,telegram_id) VALUES(?,?)",
                        (new_id, int(record["telegram_id"])),
                    )
        if {"telegram_id", "added_by"}.issubset(columns(source, "admins")):
            for record in source.execute("SELECT telegram_id, added_by FROM admins"):
                before = target.total_changes
                target.execute(
                    "INSERT OR IGNORE INTO admins(telegram_id,added_by) VALUES(?,?)",
                    (int(record["telegram_id"]), int(record["added_by"])),
                )
                results["admins"] += target.total_changes - before
        target.commit()
        return results
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: merge-unified-bot-db.py OLD_DELTA_DB SHARED_SANA_DB")
    result = merge(sys.argv[1], sys.argv[2])
    print(f"Shared bot DB merge: {result['added']} sites added, {result['matched']} already present, {result['admins']} admins imported (keys hidden)")
