import fcntl
import os
import time

from django.utils import timezone

from shop.models import SourceSite
from shop.services.category_normalizer import consolidate_duplicate_categories
from shop.services.source_bulk_job import JOB_DIR, LOCK_FILE, MAX_WARNINGS, _existing_urls, read_job, write_job
from shop.services.source_catalog_v20 import (
    CatalogSkip,
    import_unpriced_catalog_product,
    upsert_source_product_with_changes,
)
from shop.services.source_discovery_v19 import discover_product_urls_bounded
from shop.services.source_sync import SourceNotProductError


DISCOVERY_BUDGET = max(60, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "300")))
DISCOVERY_MAX_SITEMAPS = max(50, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "400")))
DISCOVERY_MAX_PAGES = max(50, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "600")))
PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.08")), 2.0))


def run_full_sync(job_id):
    """Run full catalog sync with bounded discovery and resilient product fetches."""
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
        state = {
            "status": "running",
            "phase": "planning",
            "sites": len(sites),
            "site_index": 0,
            "total": 0,
            "checked": 0,
            "created": 0,
            "changed": 0,
            "skipped": 0,
            "errors": 0,
            "categories_merged": 0,
            "products_recategorized": 0,
            "current_site": "",
            "discover_scanned": 0,
            "discover_found": 0,
            "discover_elapsed": 0,
            "discover_budget": DISCOVERY_BUDGET,
            "current_url": "",
            "warnings": [],
        }
        write_job(job_id, state)

        # Clean legacy duplicate categories before touching products. Runtime
        # canonicalization below prevents them from being recreated.
        cleanup = consolidate_duplicate_categories()
        state["categories_merged"] += cleanup["categories_merged"]
        state["products_recategorized"] += cleanup["products_recategorized"]
        write_job(job_id, state)

        plans = []
        for site_index, site in enumerate(sites, start=1):
            state.update(
                phase="discovering" if site.bulk_import_enabled else "planning",
                site_index=site_index,
                current_site=site.name,
                discover_scanned=0,
                discover_found=0,
                discover_elapsed=0,
                discover_budget=DISCOVERY_BUDGET,
                current_url="",
            )
            write_job(job_id, state)
            try:
                if site.bulk_import_enabled:
                    last_write = [0.0]

                    def progress(info):
                        state["phase"] = "discovering"
                        state["discover_scanned"] = int(info.get("requests") or 0)
                        state["discover_found"] = int(info.get("found") or 0)
                        state["discover_elapsed"] = int(info.get("elapsed") or 0)
                        state["discover_budget"] = int(info.get("budget") or DISCOVERY_BUDGET)
                        state["current_url"] = str(info.get("current_url") or "")[:500]
                        now = time.monotonic()
                        if now - last_write[0] >= 1.0 or state["discover_scanned"] <= 1:
                            write_job(job_id, state)
                            last_write[0] = now

                    discovered, meta = discover_product_urls_bounded(
                        site,
                        progress=progress,
                        budget_seconds=DISCOVERY_BUDGET,
                        max_sitemaps=DISCOVERY_MAX_SITEMAPS,
                        max_pages=DISCOVERY_MAX_PAGES,
                    )
                    site.last_discovered_count = len(discovered)
                    site.save(update_fields=["last_discovered_count"])
                    urls = list(dict.fromkeys([*discovered, *_existing_urls(site)]))
                    state["discover_scanned"] = int(meta.get("requests") or 0)
                    state["discover_found"] = len(discovered)
                    state["discover_elapsed"] = int(meta.get("elapsed") or 0)
                    state["discover_budget"] = int(meta.get("budget") or DISCOVERY_BUDGET)
                    if meta.get("timed_out") and len(state["warnings"]) < MAX_WARNINGS:
                        state["warnings"].append(
                            f"{site.name}: مهلت کشف کاتالوگ بعد از {state['discover_elapsed']} ثانیه تمام شد؛ "
                            f"{len(discovered)} محصول پیدا شد و محصولات قبلی هم برای Sync حفظ شدند."
                        )
                    elif not discovered and urls and len(state["warnings"]) < MAX_WARNINGS:
                        state["warnings"].append(f"{site.name}: محصول جدیدی کشف نشد؛ محصولات قبلی همگام شدند.")
                else:
                    urls = list(dict.fromkeys(_existing_urls(site)))
            except Exception as exc:
                urls = list(dict.fromkeys(_existing_urls(site)))
                if len(state["warnings"]) < MAX_WARNINGS:
                    state["warnings"].append(f"{site.name}: discovery: {str(exc)[:180]}")

            plans.append((site, urls))
            state["total"] += len(urls)
            state["current_url"] = ""
            write_job(job_id, state)

        if not state["total"]:
            state["status"] = "completed"
            state["phase"] = "completed"
            state["current_site"] = ""
            state["current_url"] = ""
            return write_job(job_id, state)

        state["phase"] = "syncing"
        write_job(job_id, state)
        for site_index, (site, urls) in enumerate(plans, start=1):
            state["site_index"] = site_index
            state["current_site"] = site.name
            for url in urls:
                state["current_url"] = str(url)[:500]
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
                        if len(state["warnings"]) < MAX_WARNINGS:
                            state["warnings"].append(f"{site.name}: بدون قیمت وارد شد: {str(exc)[:120]}")
                    except SourceNotProductError as nested:
                        state["skipped"] += 1
                        if len(state["warnings"]) < MAX_WARNINGS:
                            state["warnings"].append(f"{site.name}: {str(nested)[:180]}")
                    except Exception as nested:
                        state["errors"] += 1
                        if len(state["warnings"]) < MAX_WARNINGS:
                            state["warnings"].append(f"{site.name}: import-unpriced: {str(nested)[:160]}")
                except SourceNotProductError as exc:
                    state["skipped"] += 1
                    if len(state["warnings"]) < MAX_WARNINGS:
                        state["warnings"].append(f"{site.name}: {str(exc)[:180]}")
                except Exception as exc:
                    state["errors"] += 1
                    if len(state["warnings"]) < MAX_WARNINGS:
                        state["warnings"].append(f"{site.name}: {str(exc)[:180]}")

                state["checked"] += 1
                if state["checked"] == state["total"] or state["checked"] % 5 == 0:
                    write_job(job_id, state)
                if PRODUCT_DELAY:
                    time.sleep(PRODUCT_DELAY)

            if site.bulk_import_enabled:
                site.last_bulk_sync_at = timezone.now()
                site.save(update_fields=["last_bulk_sync_at"])

        # Safety pass for any legacy duplicates encountered while this job ran.
        cleanup = consolidate_duplicate_categories()
        state["categories_merged"] += cleanup["categories_merged"]
        state["products_recategorized"] += cleanup["products_recategorized"]
        state["status"] = "completed"
        state["phase"] = "completed"
        state["current_site"] = ""
        state["current_url"] = ""
        return write_job(job_id, state)
    except Exception as exc:
        current = read_job(job_id) or {}
        current.update({
            "status": "failed",
            "phase": "failed",
            "error": "bulk_sync_failed",
            "message": str(exc)[:700],
        })
        return write_job(job_id, current)
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_handle.close()
