#!/usr/bin/env python3
"""Improve only the Delta source-sync progress text.

The source callback remains owned by delta_source_restore; this module patches
its formatter so a long catalog discovery never looks frozen at 0/0.
"""
import delta_source_restore as source

_ORIGINAL = source._progress_text
_TERMINAL = {"completed", "failed"}


def progress_text(job):
    phase = str(job.get("phase") or "")
    status = str(job.get("status") or "")

    if phase == "discovering" and status not in _TERMINAL:
        scanned = int(job.get("discover_scanned") or 0)
        found = int(job.get("discover_found") or 0)
        elapsed = int(job.get("discover_elapsed") or 0)
        budget = int(job.get("discover_budget") or 0)
        site_index = int(job.get("site_index") or 0)
        sites = int(job.get("sites") or 0)
        lines = [
            "🔎 در حال کشف کاتالوگ Delta",
            "",
            f"🌐 منبع: {job.get('current_site') or '-'}" + (f" ({site_index}/{sites})" if sites else ""),
            f"🧭 درخواست‌های بررسی‌شده: {scanned:,}",
            f"📦 محصول پیدا شده تا اینجا: {found:,}",
            f"⏱ زمان کشف: {elapsed:,} ثانیه" + (f" / سقف {budget:,} ثانیه" if budget else ""),
            "",
            "بعد از پایان کشف، شمارنده Sync محصولات شروع می‌شود.",
        ]
        return "\n".join(lines)

    text = _ORIGINAL(job)
    if phase == "syncing" and job.get("current_url"):
        return text + "\n🔗 در حال Sync محصول..."
    return text


def install():
    if getattr(source, "_delta_sync_progress_v19_installed", False):
        return
    source._progress_text = progress_text
    source._delta_sync_progress_v19_installed = True
