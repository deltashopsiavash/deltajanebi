#!/usr/bin/env python3
"""Verify shared DB migration never overwrites SanaShop data or mixes site admins."""
import importlib.util
import sqlite3
import tempfile
from pathlib import Path


def load_merger():
    spec = importlib.util.spec_from_file_location("unified_db_merge", "scripts/merge-unified-bot-db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_database(path):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE sites(id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL UNIQUE, api_key TEXT NOT NULL);
        CREATE TABLE admins(telegram_id INTEGER PRIMARY KEY, added_by INTEGER NOT NULL);
        CREATE TABLE site_admins(site_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL UNIQUE, UNIQUE(site_id,telegram_id));
    """)
    db.commit()
    return db


def main():
    merger = load_merger()
    with tempfile.TemporaryDirectory() as base:
        a = Path(base) / "sana.sqlite3"
        b = Path(base) / "delta.sqlite3"
        with create_database(a) as db:
            db.execute("INSERT INTO sites VALUES (1, 'Sana', 'https://sana.example', 'KEEP_SANA_KEY')")
            db.execute("INSERT INTO sites VALUES (2, 'Delta old link', 'https://delta.example', 'KEEP_DELTA_KEY')")
            db.execute("INSERT INTO site_admins VALUES (1, 100)")
            db.commit()
        with create_database(b) as db:
            db.execute("INSERT INTO sites VALUES (1, 'Delta legacy', 'https://delta.example/', 'DONT_OVERRIDE')")
            db.execute("INSERT INTO sites VALUES (2, 'Delta new', 'https://newdelta.example', 'NEW_DELTA_KEY')")
            db.execute("INSERT INTO site_admins VALUES (2, 200)")
            db.execute("INSERT INTO admins VALUES (300, 123)")
            db.commit()
        counts = merger.merge(str(b), str(a))
        assert counts["added"] == 1 and counts["matched"] == 1, counts
        with sqlite3.connect(a) as db:
            rows = db.execute("SELECT id,base_url,api_key FROM sites ORDER BY id").fetchall()
            assert len(rows) == 3 and rows[0][2] == "KEEP_SANA_KEY"
            assert rows[1][2] == "KEEP_DELTA_KEY", "Existing connected site's key must survive"
            assert rows[2][2] == "NEW_DELTA_KEY"
            assert db.execute("SELECT site_id FROM site_admins WHERE telegram_id=200").fetchone()[0] == rows[2][0]
            assert db.execute("SELECT 1 FROM site_admins WHERE telegram_id=100").fetchone()
            assert db.execute("SELECT 1 FROM admins WHERE telegram_id=300").fetchone()
        assert merger.merge(str(b), str(a))["added"] == 0
        with sqlite3.connect(b) as db:
            assert db.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 2
    print("Shared Sana/Delta database migration: OK")


if __name__ == "__main__":
    main()
