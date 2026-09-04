"""Delta source-catalog full sync v27.

This is a clean orchestration layer: durable DB job state, deployment-wide lock,
OS-isolated products, OS-isolated discovery with partial URL recovery, one
pre/final cleanup pass, and explicit all-sources or single-source scope.
"""
import os
import time

from django.utils import timezone

from shop.models import Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services import category_v22
from shop.services import source_identity_v21 as legacy_identity
from shop.services import source_identity_v22 as identity
from shop.services.source_isolation_v26 import (
    PRODUCT_WALL_SECONDS,
    run_discovery_isolated,
    run_product_isolated,
)
from shop.services.source_job_store_v26 import MAX_WARNINGS, read_job, write_job
from shop.services.source_sync_lock import catalog_sync_lock


DISCOVERY_BUDGET = max(120, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "1800")))
DISCOVERY_MAX_SITEMAPS = max(100, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "2000")))
DISCOVERY_MAX_PAGES = max(100, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "5000")))
PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.02")), 1.0))
LOCK_WAIT_SECONDS = max(30, min(int(os.getenv("DELTA_SOURCE_MANUAL_LOCK_WAIT", "600")), 3600))


def _warn(state, text):
    warnings = state.setdefault("warnings", [])
    value = str(text or "").strip()
    if value and value not in warnings and len(warnings) < MAX_WARNINGS:
        warnings.append(value[:900])


def _save(job_id, state, **updates):
    if updates:
        state.update(updates)
    state["status"] = state.get("status") or "running"
    return write_job(job_id, state)


def _existing_urls(site):
    urls = list(
        ProductSourceOffer.objects.filter(source_site=site)
        .exclude(source_url="")
        .order_by("id")
        .values_list("source_url", flat=True)
    )
    urls.extend(
        Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname)
        .exclude(source_url="")
        .order_by("id")
        .values_list("source_url", flat=True)
    )
    return list(dict.fromkeys(str(value) for value in urls if value))


def _apply_cleanup_stats(state, stats):
    for key in (
        "categories_merged",
        "products_recategorized",
        "products_merged",
        "products_deleted",
        "products_split",
        "offers_moved",
        "offers_backfilled",
        "identity_refreshed",
        "offers_reassigned",
    ):
        if key in stats:
            state[key] = int(state.get(key) or 0) + int(stats.get(key) or 0)


def _pre_cleanup(job_id, state):
    _save(
        job_id,
        state,
        phase="cleanup_pre",
        current_site="",
        current_url="",
        phase_total=2,
        phase_checked=0,
        item_started_at=0,
        item_mode="database_cleanup",
    )
    backfilled = legacy_identity.backfill_existing_offers()
    state["offers_backfilled"] = int(state.get("offers_backfilled") or 0) + int(backfilled or 0)
    state["phase_checked"] = 1
    _save(job_id, state)

    category_stats = category_v22.consolidate_sibling_duplicates()
    _apply_cleanup_stats(state, category_stats)
    identity_stats = identity.consolidate_duplicate_products()
    _apply_cleanup_stats(state, identity_stats)
    state["phase_checked"] = 2
    _save(job_id, state)


def _final_cleanup(job_id, state):
    _save(
        job_id,
        state,
        phase="cleanup_final",
        current_site="",
        current_url="",
        phase_total=2,
        phase_checked=0,
        item_started_at=0,
        item_mode="database_cleanup",
    )
    category_stats = category_v22.consolidate_sibling_duplicates()
    _apply_cleanup_stats(state, category_stats)
    state["phase_checked"] = 1
    _save(job_id, state)
    identity_stats = identity.consolidate_duplicate_products()
    _apply_cleanup_stats(state, identity_stats)
    state["phase_checked"] = 2
    _save(job_id, state)


def _sync_urls(job_id, state, site, urls, phase):
    urls = list(dict.fromkeys(str(value) for value in (urls or []) if value))
    state.update({
        "phase": phase,
        "phase_total": len(urls),
        "phase_checked": 0,
        "current_site": site.name,
        "current_url": "",
        "item_started_at": 0,
        "item_timeout": int(PRODUCT_WALL_SECONDS),
        "item_mode": "isolated_process",
    })
    _save(job_id, state)

    for url in urls:
        state.update({
            "current_url": url[:500],
            "item_started_at": time.time(),
            "item_timeout": int(PRODUCT_WALL_SECONDS),
            "item_mode": "isolated_process",
        })
        _save(job_id, state)

        result = run_product_isolated(site, url)
        state["created"] = int(state.get("created") or 0) + int(result.get("created") or 0)
        state["changed"] = int(state.get("changed") or 0) + int(result.get("changed") or 0)
        state["skipped"] = int(state.get("skipped") or 0) + int(result.get("skipped") or 0)
        state["errors"] = int(state.get("errors") or 0) + int(result.get("errors") or 0)
        if result.get("warning"):
            _warn(state, result["warning"])

        state["checked"] = int(state.get("checked") or 0) + 1
        state["phase_checked"] = int(state.get("phase_checked") or 0) + 1
        state["item_started_at"] = 0
        _save(job_id, state)
        if PRODUCT_DELAY:
            time.sleep(PRODUCT_DELAY)


def _discover(job_id, state, site):
    state.update({
        "phase": "discovering",
        "current_site": site.name,
        "current_url": "",
        "discover_scanned": 0,
        "discover_found": 0,
        "discover_elapsed": 0,
        "discover_budget": DISCOVERY_BUDGET,
        "item_started_at": time.time(),
        "item_timeout": int(DISCOVERY_BUDGET),
        "item_mode": "isolated_discovery",
    })
    _save(job_id, state)

    def progress(info):
        state.update({
            "phase": "discovering",
            "current_url": str(info.get("current_url") or "")[:500],
            "discover_scanned": int(info.get("requests") or 0),
            "discover_found": int(info.get("found") or 0),
            "discover_elapsed": int(info.get("elapsed") or 0),
            "discover_budget": int(info.get("budget") or DISCOVERY_BUDGET),
        })
        _save(job_id, state)

    urls, meta, warning = run_discovery_isolated(
        site,
        budget_seconds=DISCOVERY_BUDGET,
        max_sitemaps=DISCOVERY_MAX_SITEMAPS,
        max_pages=DISCOVERY_MAX_PAGES,
        progress=progress,
    )
    state.update({
        "discover_scanned": int(meta.get("requests") or 0),
        "discover_found": len(urls),
        "discover_elapsed": int(meta.get("elapsed") or 0),
        "discover_budget": int(meta.get("budget") or DISCOVERY_BUDGET),
        "current_url": "",
        "item_started_at": 0,
    })
    if warning:
        _warn(state, warning)
    if meta.get("stalled") or meta.get("hard_timed_out"):
        state["errors"] = int(state.get("errors") or 0) + 1
    _save(job_id, state)
    return urls


def _selected_sites(previous):
    try:
        target_id = max(0, int(previous.get("target_source_site_id") or 0))
    except (TypeError, ValueError):
        target_id = 0
    rows = SourceSite.objects.filter(is_active=True).order_by("id")
    if target_id:
        rows = rows.filter(pk=target_id)
    sites = list(rows)
    if target_id and not sites:
        raise RuntimeError("سایت منبع انتخاب‌شده حذف یا غیرفعال شده است.")
    return sites, target_id


def _run_locked(job_id):
    previous = read_job(job_id) or {}
    sites, target_id = _selected_sites(previous)
    target_name = str(previous.get("target_source_site_name") or "")[:200]
    if target_id and sites:
        target_name = sites[0].name

    state = {
        "status": "running",
        "phase": "preparing",
        "sites": 0,
        "site_index": 0,
        "total": 0,
        "checked": 0,
        "created": 0,
        "changed": 0,
        "skipped": 0,
        "errors": 0,
        "categories_merged": 0,
        "products_recategorized": 0,
        "products_merged": 0,
        "products_deleted": 0,
        "products_split": 0,
        "offers_moved": 0,
        "offers_backfilled": 0,
        "identity_refreshed": 0,
        "current_site": "",
        "current_url": "",
        "phase_total": 0,
        "phase_checked": 0,
        "discover_scanned": 0,
        "discover_found": 0,
        "discover_elapsed": 0,
        "discover_budget": DISCOVERY_BUDGET,
        "item_started_at": 0,
        "item_timeout": 0,
        "item_mode": "",
        "warnings": list(previous.get("warnings") or [])[:MAX_WARNINGS],
        "target_source_site_id": target_id,
        "target_source_site_name": target_name,
        "sync_scope": "single_source" if target_id else "all_sources",
        "engine_version": 27,
        "job_store": "database",
    }
    _save(job_id, state)

    _pre_cleanup(job_id, state)
    known_by_site = [(site, _existing_urls(site)) for site in sites]
    state["sites"] = len(sites)
    state["total"] = sum(len(urls) for _, urls in known_by_site)
    _save(job_id, state, phase="preparing", phase_checked=0, phase_total=0, item_mode="")

    for site_index, (site, known_urls) in enumerate(known_by_site, start=1):
        state.update({
            "site_index": site_index,
            "current_site": site.name,
            "current_url": "",
            "discover_scanned": 0,
            "discover_found": 0,
            "discover_elapsed": 0,
            "discover_budget": DISCOVERY_BUDGET,
        })
        _save(job_id, state)

        if known_urls:
            _sync_urls(job_id, state, site, known_urls, "syncing_known")

        discovered = []
        if site.bulk_import_enabled:
            discovered = _discover(job_id, state, site)
            site.last_discovered_count = len(discovered)
            site.save(update_fields=["last_discovered_count"])

        known_set = set(known_urls)
        new_urls = [url for url in dict.fromkeys(discovered) if url not in known_set]
        if new_urls:
            state["total"] = int(state.get("total") or 0) + len(new_urls)
            _save(job_id, state)
            _sync_urls(job_id, state, site, new_urls, "syncing_new")
        elif site.bulk_import_enabled and not discovered:
            _warn(state, f"{site.name}: محصول جدیدی کشف نشد؛ محصولات قبلی همگام شدند.")
            _save(job_id, state)

        if site.bulk_import_enabled:
            site.last_bulk_sync_at = timezone.now()
            site.save(update_fields=["last_bulk_sync_at"])

    _final_cleanup(job_id, state)
    state.update({
        "status": "completed",
        "phase": "completed",
        "current_site": "",
        "current_url": "",
        "item_started_at": 0,
        "item_mode": "",
    })
    return write_job(job_id, state)


def run_full_sync(job_id):
    """Wait for the shared catalog lock while emitting durable heartbeats."""
    wait_started = time.monotonic()
    while True:
        with catalog_sync_lock() as acquired:
            if acquired:
                try:
                    return _run_locked(job_id)
                except Exception as exc:
                    current = read_job(job_id) or {}
                    current.update({
                        "status": "failed",
                        "phase": "failed",
                        "error": "bulk_sync_failed_v27",
                        "message": str(exc)[:1200],
                        "item_started_at": 0,
                    })
                    return write_job(job_id, current)

        elapsed = int(time.monotonic() - wait_started)
        if elapsed >= LOCK_WAIT_SECONDS:
            current = read_job(job_id) or {}
            current.update({
                "status": "failed",
                "phase": "failed",
                "error": "catalog_lock_timeout",
                "message": "همگام‌سازی خودکار/دستی قبلی قفل کاتالوگ را در زمان مجاز آزاد نکرد.",
            })
            return write_job(job_id, current)
        current = read_job(job_id) or {}
        current.update({
            "status": "running",
            "phase": "waiting_lock",
            "message": "در انتظار آزاد شدن Sync کاتالوگ دیگر...",
            "lock_wait_elapsed": elapsed,
            "item_started_at": 0,
            "item_mode": "lock_wait",
        })
        write_job(job_id, current)
        time.sleep(1.0)
