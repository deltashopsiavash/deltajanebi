#!/usr/bin/env python3
"""Phase-aware Telegram progress for Delta full catalog sync v22."""
import re

import delta_source_restore as source


def _ratio(done, total):
    total = max(0, int(total or 0))
    done = max(0, int(done or 0))
    return min(1.0, done / total) if total else 0.0


def _overall_percent(job):
    status = str(job.get("status") or "")
    if status == "completed":
        return 100
    sites = max(1, int(job.get("sites") or 1))
    site_index = max(1, min(sites, int(job.get("site_index") or 1)))
    phase = str(job.get("phase") or "")

    if phase == "preparing":
        local = 0.0
    elif phase == "syncing_known":
        local = 0.45 * _ratio(job.get("phase_checked"), job.get("phase_total"))
    elif phase == "discovering":
        elapsed_ratio = _ratio(job.get("discover_elapsed"), job.get("discover_budget"))
        # Discovery has an explicit heartbeat. Cap its visual share below 100%
        # because URL processing still follows after discovery completes.
        local = 0.45 + 0.20 * min(0.97, elapsed_ratio)
    elif phase == "syncing_new":
        local = 0.65 + 0.35 * _ratio(job.get("phase_checked"), job.get("phase_total"))
    elif status == "failed":
        # Keep the last meaningful progress rather than falsely showing 100%.
        local = min(0.99, _ratio(job.get("checked"), job.get("total")))
    else:
        local = min(0.99, _ratio(job.get("checked"), job.get("total")))

    overall = ((site_index - 1) + min(0.999, max(0.0, local))) / sites
    return max(0, min(99, int(overall * 100)))


def install():
    if getattr(source, "_delta_sync_progress_v22_installed", False):
        return
    previous = source._progress_text

    def progress_text(job):
        text = previous(job)
        percent = _overall_percent(job)
        width = 16
        filled = min(width, int(width * percent / 100))
        bar = "█" * filled + "░" * (width - filled)
        text = re.sub(r"(?m)^[█░]+\s+\d+%$", f"{bar} {percent}%", text, count=1)

        status = str(job.get("status") or "")
        phase = str(job.get("phase") or "")
        extras = []
        if status not in {"completed", "failed"}:
            sites = max(0, int(job.get("sites") or 0))
            site_index = max(0, int(job.get("site_index") or 0))
            if sites and site_index:
                extras.append(f"🧭 منبع: {site_index}/{sites}")
            if phase in {"syncing_known", "syncing_new"}:
                done = int(job.get("phase_checked") or 0)
                total = int(job.get("phase_total") or 0)
                extras.append(f"📍 پیشرفت همین مرحله: {done:,} / {total:,}")
            elif phase == "discovering":
                extras.append("✅ نوار پیشرفت در مرحله کشف هم زنده است و متوقف نمی‌ماند.")

        if status in {"completed", "failed"}:
            deleted = int(job.get("products_deleted") or 0)
            split = int(job.get("products_split") or 0)
            refreshed = int(job.get("identity_refreshed") or 0)
            if deleted:
                extras.append(f"🗑 ردیف محصول تکراری حذف‌شده: {deleted:,}")
            if split:
                extras.append(f"🧬 واریانت اشتباه جداشده: {split:,}")
            if refreshed:
                extras.append(f"🔎 شناسه مدل/ظرفیت بازبینی‌شده: {refreshed:,}")

        if extras:
            text += "\n" + "\n".join(extras)
        return text

    source._progress_text = progress_text
    source._delta_sync_progress_v22_installed = True
