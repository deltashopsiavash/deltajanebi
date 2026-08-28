#!/usr/bin/env python3
"""Append v20 category-cleanup results to the existing sync progress text."""
import delta_source_restore as source

_ORIGINAL = source._progress_text


def progress_text(job):
    text = _ORIGINAL(job)
    if str(job.get("status") or "") in {"completed", "failed"}:
        merged = int(job.get("categories_merged") or 0)
        moved = int(job.get("products_recategorized") or 0)
        if merged or moved:
            text += (
                f"\n🧹 دسته‌های تکراری ادغام‌شده: {merged:,}"
                f"\n🗂 محصولات منتقل‌شده به دسته صحیح: {moved:,}"
            )
    return text


def install():
    if getattr(source, "_delta_sync_progress_v20_installed", False):
        return
    source._progress_text = progress_text
    source._delta_sync_progress_v20_installed = True
