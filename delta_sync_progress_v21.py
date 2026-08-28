#!/usr/bin/env python3
"""Live progress details for Delta catalog sync v21."""
from urllib.parse import urlparse

import delta_source_restore as source


def _short_url(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        if parsed.hostname:
            path = parsed.path or "/"
            if len(path) > 80:
                path = path[:77] + "..."
            return f"{parsed.hostname}{path}"
    except Exception:
        pass
    return text[:100]


def install():
    if getattr(source, "_delta_sync_progress_v21_installed", False):
        return
    original = source._progress_text

    def progress_text(job):
        text = original(job)
        phase = str(job.get("phase") or "")
        extra = []

        if phase == "preparing":
            extra.append("🧩 مرحله: آماده‌سازی منابع و ادغام داده‌های قدیمی")
        elif phase == "syncing_known":
            extra.append("⚡ مرحله: بروزرسانی محصولات قبلی این منبع")
        elif phase == "discovering":
            elapsed = int(job.get("discover_elapsed") or 0)
            budget = int(job.get("discover_budget") or 0)
            extra.extend([
                "🔎 مرحله: جستجوی محصولات جدید این منبع",
                f"🧭 درخواست‌های کشف: {int(job.get('discover_scanned') or 0):,}",
                f"📥 URL محصول پیدا‌شده: {int(job.get('discover_found') or 0):,}",
                f"⏱ زمان کشف: {elapsed:,} / {budget:,} ثانیه",
            ])
        elif phase == "syncing_new":
            extra.append("🆕 مرحله: واردکردن محصولات تازه کشف‌شده")

        current = _short_url(job.get("current_url"))
        if current and phase not in {"completed", "failed"}:
            extra.append(f"🔗 در حال بررسی: {current}")

        if str(job.get("status") or "") in {"completed", "failed"}:
            merged = int(job.get("products_merged") or 0)
            moved = int(job.get("offers_moved") or 0)
            backfilled = int(job.get("offers_backfilled") or 0)
            if merged or moved or backfilled:
                extra.extend([
                    f"🧩 محصولات تکراری یکی‌شده: {merged:,}",
                    f"🔗 رکورد منبع منتقل‌شده: {moved:,}",
                    f"🗃 منابع قدیمی ثبت‌شده برای تجمیع: {backfilled:,}",
                ])

        return text + (("\n" + "\n".join(extra)) if extra else "")

    source._progress_text = progress_text
    source._delta_sync_progress_v21_installed = True
