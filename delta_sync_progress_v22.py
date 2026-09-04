#!/usr/bin/env python3
"""Phase-aware Telegram progress for the durable Delta catalog engine v26."""
import re
import time

import delta_source_restore as source


def _ratio(done, total):
    total = max(0, int(total or 0))
    done = max(0, int(done or 0))
    return min(1.0, done / total) if total else 0.0


def _overall_percent(job):
    status = str(job.get("status") or "")
    if status == "completed":
        return 100
    phase = str(job.get("phase") or "")
    sites = max(1, int(job.get("sites") or 1))
    raw_index = int(job.get("site_index") or 0)

    if phase in {"queued", "waiting_worker", "waiting_lock", "preparing"}:
        return 0
    if phase == "cleanup_pre":
        return min(2, int(2 * _ratio(job.get("phase_checked"), job.get("phase_total"))))
    if phase == "cleanup_final":
        return 97 + min(2, int(2 * _ratio(job.get("phase_checked"), job.get("phase_total"))))

    site_index = max(1, min(sites, raw_index or 1))
    if phase == "syncing_known":
        local = 0.05 + 0.40 * _ratio(job.get("phase_checked"), job.get("phase_total"))
    elif phase == "discovering":
        elapsed_ratio = _ratio(job.get("discover_elapsed"), job.get("discover_budget"))
        local = 0.45 + 0.20 * min(0.97, elapsed_ratio)
    elif phase == "syncing_new":
        local = 0.65 + 0.32 * _ratio(job.get("phase_checked"), job.get("phase_total"))
    elif status in {"failed", "cancelled"}:
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
        if status not in {"completed", "failed", "cancelled"}:
            sites = max(0, int(job.get("sites") or 0))
            site_index = max(0, int(job.get("site_index") or 0))
            if sites and site_index:
                extras.append(f"🧭 منبع: {site_index}/{sites}")

            if phase == "waiting_worker":
                extras.append("🧰 در صف supervisor اختصاصی Sync...")
            elif phase == "waiting_lock":
                extras.append(f"🔐 در انتظار آزاد شدن قفل کاتالوگ: {int(job.get('lock_wait_elapsed') or 0)} ثانیه")
            elif phase == "cleanup_pre":
                extras.append("🧹 پاکسازی و یکپارچه‌سازی اولیه کاتالوگ...")
            elif phase == "cleanup_final":
                extras.append("🧹 پاکسازی نهایی دسته‌ها و محصولات تکراری...")
            elif phase in {"syncing_known", "syncing_new"}:
                done = int(job.get("phase_checked") or 0)
                total = int(job.get("phase_total") or 0)
                extras.append(f"📍 پیشرفت همین مرحله: {done:,} / {total:,}")
                started = float(job.get("item_started_at") or 0)
                timeout = int(job.get("item_timeout") or 0)
                if started > 0:
                    elapsed = max(0, int(time.time() - started))
                    extras.append(f"⏱ محصول فعلی: {elapsed:,} / {timeout:,} ثانیه")
                    extras.append("🛡 هر محصول Process جدا دارد؛ در سقف زمان Kill و رد می‌شود.")
            elif phase == "discovering":
                extras.append("🧭 کشف کاتالوگ در Process جدا و دارای watchdog سیستم‌عامل است.")
                extras.append(f"🧭 درخواست‌های کشف: {int(job.get('discover_scanned') or 0):,}")
                extras.append(f"📥 URL محصول پیدا‌شده: {int(job.get('discover_found') or 0):,}")
                extras.append(
                    f"⏱ زمان کشف: {int(job.get('discover_elapsed') or 0):,} / "
                    f"{int(job.get('discover_budget') or 0):,} ثانیه"
                )

            heartbeat_age = job.get("heartbeat_age")
            if heartbeat_age is not None and int(heartbeat_age) >= 15:
                extras.append(f"💓 آخرین heartbeat سرور: {int(heartbeat_age)} ثانیه قبل")

        if status in {"completed", "failed", "cancelled"}:
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
