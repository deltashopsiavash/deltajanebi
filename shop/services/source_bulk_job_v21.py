import fcntl
import os
import time

from django.utils import timezone

from shop.models import SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services.category_v21 import consolidate_sibling_duplicates
from shop.services.source_bulk_job import (
    JOB_DIR,
    LOCK_FILE,
    MAX_WARNINGS,
    _existing_urls as _legacy_existing_urls,
    read_job,
    write_job,
)
from shop.services.source_catalog_v21 import (
    CatalogSkip,
    import_unpriced_catalog_product,
    upsert_source_product_with_changes,
)
from shop.services.source_discovery_v19 import discover_product_urls_bounded
from shop.services.source_identity_v21 import (
    aggregate_product,
    backfill_existing_offers,
    consolidate_duplicate_products,
)
from shop.services.source_sync import SourceNotProductError


os.environ.setdefault("SOURCE_REQUEST_TIMEOUT", "10")
DISCOVERY_BUDGET = max(30, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "75")))
DISCOVERY_MAX_SITEMAPS = max(30, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "180")))
DISCOVERY_MAX_PAGES = max(40, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "220")))
PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.03")), 1.0))


def _existing_urls(site):
    """Return legacy Product URLs plus every v21 offer URL for this source."""
    urls = list(
        ProductSourceOffer.objects.filter(source_site=site)
        .exclude(source_url="")
        .values_list("source_url", flat=True)
    )
    urls.extend(_legacy_existing_urls(site))
    return list(dict.fromkeys(urls))


def _warn(state, text):
    if len(state["warnings"]) < MAX_WARNINGS:
        state["warnings"].append(str(text)[:260])


def _mark_source_missing(site, url):
    offer = (
        ProductSourceOffer.objects.filter(source_site=site, source_url=url)
        .select_related("product")
        .first()
    )
    if not offer:
        return
    product = offer.product
    offer.stock = 0
    offer.is_active = False
    offer.last_seen_at = timezone.now()
    offer.save(update_fields=["stock", "is_active", "last_seen_at", "updated_at"])
    aggregate_product(product)


def _sync_urls(job_id, state, site, urls, phase):
    state["phase"] = phase
    state["current_site"] = site.name
    write_job(job_id, state)
    for url in urls:
        state["current_url"] = str(url)[:500]
        # Heartbeat before network access so a slow product page is visible.
        write_job(job_id, state)
        try:
            _, created, changes = upsert_source_product_with_changes(site, url)
            if created:
                state["created"] += 1
            if created or changes:
                state["changed"] += 1
        except CatalogSkip as exc:
            try:
                _, created = import_unpriced_catalog_product(site, url)
                if created:
                    state["created"] += 1
                    state["changed"] += 1
                _warn(state, f"{site.name}: بدون قیمت وارد/به‌روزرسانی شد: {exc}")
            except SourceNotProductError as nested:
                _mark_source_missing(site, url)
                state["skipped"] += 1
                _warn(state, f"{site.name}: {nested}")
            except Exception as nested:
                state["errors"] += 1
                _warn(state, f"{site.name}: import-unpriced: {nested}")
        except SourceNotProductError as exc:
            # A confirmed non-product/removed page should no longer contribute
            # stale stock. Network/time-out errors are handled separately and
            # deliberately preserve the last known offer.
            _mark_source_missing(site, url)
            state["skipped"] += 1
            _warn(state, f"{site.name}: {exc}")
        except Exception as exc:
            state["errors"] += 1
            _warn(state, f"{site.name}: {exc}")

        state["checked"] += 1
        write_job(job_id, state)
        if PRODUCT_DELAY:
            time.sleep(PRODUCT_DELAY)


def run_full_sync(job_id):
    """Incremental full sync: existing products first, discovery second per site.

    v20 discovered every source completely before processing the first product.
    v21 refreshes all known Product/Offer URLs immediately, then searches only
    for additions on that source, keeping the public counter moving from the
    start and preserving independent stock for every source behind one product.
    """
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("a+")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return write_job(job_id, {
                "status": "failed",
                "phase": "failed",
                "error": "another_sync_is_running",
                "message": "یک همگام‌سازی دیگر در حال اجراست.",
            })

        sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))

        # Backfill first so known_by_site includes secondary source URLs from
        # already-merged legacy products in this very same run.
        pre_backfilled = backfill_existing_offers()
        known_by_site = [(site, _existing_urls(site)) for site in sites]
        state = {
            "status": "running",
            "phase": "preparing",
            "sites": len(sites),
            "site_index": 0,
            "total": sum(len(urls) for _, urls in known_by_site),
            "checked": 0,
            "created": 0,
            "changed": 0,
            "skipped": 0,
            "errors": 0,
            "categories_merged": 0,
            "products_recategorized": 0,
            "products_merged": 0,
            "offers_moved": 0,
            "offers_backfilled": pre_backfilled,
            "current_site": "",
            "discover_scanned": 0,
            "discover_found": 0,
            "discover_elapsed": 0,
            "discover_budget": DISCOVERY_BUDGET,
            "current_url": "",
            "warnings": [],
        }
        write_job(job_id, state)

        cleanup = consolidate_sibling_duplicates()
        state["categories_merged"] += cleanup["categories_merged"]
        state["products_recategorized"] += cleanup["products_recategorized"]
        duplicate_cleanup = consolidate_duplicate_products()
        state["products_merged"] += duplicate_cleanup["products_merged"]
        state["offers_moved"] += duplicate_cleanup["offers_moved"]
        write_job(job_id, state)

        for site_index, (site, known_urls) in enumerate(known_by_site, start=1):
            state.update(
                site_index=site_index,
                current_site=site.name,
                current_url="",
                discover_scanned=0,
                discover_found=0,
                discover_elapsed=0,
                discover_budget=DISCOVERY_BUDGET,
            )

            if known_urls:
                _sync_urls(job_id, state, site, known_urls, "syncing_known")

            discovered = []
            if site.bulk_import_enabled:
                state["phase"] = "discovering"
                state["current_url"] = ""
                write_job(job_id, state)
                last_write = [0.0]

                def progress(info):
                    state["phase"] = "discovering"
                    state["discover_scanned"] = int(info.get("requests") or 0)
                    state["discover_found"] = int(info.get("found") or 0)
                    state["discover_elapsed"] = int(info.get("elapsed") or 0)
                    state["discover_budget"] = int(info.get("budget") or DISCOVERY_BUDGET)
                    state["current_url"] = str(info.get("current_url") or "")[:500]
                    now = time.monotonic()
                    if now - last_write[0] >= 0.8 or state["discover_scanned"] <= 1:
                        write_job(job_id, state)
                        last_write[0] = now

                try:
                    discovered, meta = discover_product_urls_bounded(
                        site,
                        progress=progress,
                        budget_seconds=DISCOVERY_BUDGET,
                        max_sitemaps=DISCOVERY_MAX_SITEMAPS,
                        max_pages=DISCOVERY_MAX_PAGES,
                    )
                    site.last_discovered_count = len(discovered)
                    site.save(update_fields=["last_discovered_count"])
                    state["discover_scanned"] = int(meta.get("requests") or 0)
                    state["discover_found"] = len(discovered)
                    state["discover_elapsed"] = int(meta.get("elapsed") or 0)
                    state["discover_budget"] = int(meta.get("budget") or DISCOVERY_BUDGET)
                    if meta.get("timed_out"):
                        _warn(
                            state,
                            f"{site.name}: کشف کاتالوگ در سقف {state['discover_elapsed']} ثانیه متوقف شد؛ "
                            f"{len(discovered)} URL پیدا شد و Sync ادامه پیدا کرد.",
                        )
                except Exception as exc:
                    _warn(state, f"{site.name}: discovery: {exc}")
                    discovered = []

            known_set = set(known_urls)
            new_urls = [url for url in dict.fromkeys(discovered) if url not in known_set]
            if new_urls:
                state["total"] += len(new_urls)
                write_job(job_id, state)
                _sync_urls(job_id, state, site, new_urls, "syncing_new")
            elif site.bulk_import_enabled and not discovered:
                _warn(state, f"{site.name}: محصول جدیدی کشف نشد؛ محصولات قبلی همگام شدند.")

            if site.bulk_import_enabled:
                site.last_bulk_sync_at = timezone.now()
                site.save(update_fields=["last_bulk_sync_at"])

            duplicate_cleanup = consolidate_duplicate_products()
            state["products_merged"] += duplicate_cleanup["products_merged"]
            state["offers_moved"] += duplicate_cleanup["offers_moved"]
            state["current_url"] = ""
            write_job(job_id, state)

        cleanup = consolidate_sibling_duplicates()
        state["categories_merged"] += cleanup["categories_merged"]
        state["products_recategorized"] += cleanup["products_recategorized"]
        duplicate_cleanup = consolidate_duplicate_products()
        state["products_merged"] += duplicate_cleanup["products_merged"]
        state["offers_moved"] += duplicate_cleanup["offers_moved"]
        state.update(status="completed", phase="completed", current_site="", current_url="")
        return write_job(job_id, state)
    except Exception as exc:
        current = read_job(job_id) or {}
        current.update({
            "status": "failed",
            "phase": "failed",
            "error": "bulk_sync_failed_v21",
            "message": str(exc)[:700],
        })
        return write_job(job_id, current)
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_handle.close()
