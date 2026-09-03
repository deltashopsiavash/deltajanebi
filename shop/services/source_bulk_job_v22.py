"""Full catalog sync v24.

Keeps v22's category/identity policies, a hard per-product watchdog, and now a
PostgreSQL advisory lock shared with the periodic sync container. The old /tmp
flock only protected processes inside the web container, so manual and automatic
syncs could run at the same time and exhaust CPU/RAM/network resources.
"""
import os
import signal
import threading
import time
from contextlib import contextmanager

# Import the v22 catalog bridge before v21 bulk so all legacy call sites have the
# same identity/category behavior in this process.
from shop.services import source_catalog_v22 as catalog
from shop.services import source_bulk_job_v21 as base
from shop.services import category_v22
from shop.services import source_identity_v22 as identity
from shop.services.source_sync import SourceNotProductError
from shop.services.source_sync_lock import catalog_sync_lock

base.upsert_source_product_with_changes = catalog.upsert_source_product_with_changes
base.import_unpriced_catalog_product = catalog.import_unpriced_catalog_product
base.CatalogSkip = catalog.CatalogSkip
base.consolidate_sibling_duplicates = category_v22.consolidate_sibling_duplicates
base.consolidate_duplicate_products = identity.consolidate_duplicate_products
base.aggregate_product = identity.aggregate_product

# Discovery keeps a generous catalog-wide allowance, while each individual
# product below has its own much smaller hard deadline.
base.DISCOVERY_BUDGET = max(300, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "1800")))
base.DISCOVERY_MAX_SITEMAPS = max(250, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "2000")))
base.DISCOVERY_MAX_PAGES = max(300, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "5000")))
base.PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.02")), 1.0))
PRODUCT_WALL_SECONDS = max(12.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_WALL_TIMEOUT", "35")), 180.0))


class ProductDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def _product_deadline():
    """Enforce a true wall-clock deadline for one URL on Linux/main thread."""
    supported = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not supported:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def handler(_signum, _frame):
        raise ProductDeadlineExceeded(
            f"product_sync_timeout: سقف {int(PRODUCT_WALL_SECONDS)} ثانیه برای این محصول تمام شد"
        )

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, PRODUCT_WALL_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _sync_urls(job_id, state, site, urls, phase):
    urls = list(urls or [])
    state["phase"] = phase
    state["phase_total"] = len(urls)
    state["phase_checked"] = 0
    state["current_site"] = site.name
    state["item_started_at"] = 0
    state["item_timeout"] = int(PRODUCT_WALL_SECONDS)
    base.write_job(job_id, state)

    for url in urls:
        state["current_url"] = str(url)[:500]
        state["item_started_at"] = time.time()
        state["item_timeout"] = int(PRODUCT_WALL_SECONDS)
        base.write_job(job_id, state)
        try:
            with _product_deadline():
                try:
                    _, created, changes = catalog.upsert_source_product_with_changes(site, url)
                    if created:
                        state["created"] += 1
                    if created or changes:
                        state["changed"] += 1
                except catalog.CatalogSkip as exc:
                    try:
                        _, created = catalog.import_unpriced_catalog_product(site, url)
                        if created:
                            state["created"] += 1
                            state["changed"] += 1
                        base._warn(state, f"{site.name}: بدون قیمت وارد/به‌روزرسانی شد: {exc}")
                    except SourceNotProductError as nested:
                        base._mark_source_missing(site, url)
                        state["skipped"] += 1
                        base._warn(state, f"{site.name}: {nested}")
                    except ProductDeadlineExceeded:
                        raise
                    except Exception as nested:
                        state["errors"] += 1
                        base._warn(state, f"{site.name}: import-unpriced: {nested}")
        except ProductDeadlineExceeded as exc:
            state["errors"] += 1
            base._warn(state, f"{site.name}: {exc}; محصول رد شد و Sync ادامه یافت.")
        except SourceNotProductError as exc:
            base._mark_source_missing(site, url)
            state["skipped"] += 1
            base._warn(state, f"{site.name}: {exc}")
        except Exception as exc:
            state["errors"] += 1
            base._warn(state, f"{site.name}: {exc}")

        state["checked"] += 1
        state["phase_checked"] += 1
        state["item_started_at"] = 0
        base.write_job(job_id, state)
        if base.PRODUCT_DELAY:
            time.sleep(base.PRODUCT_DELAY)


base._sync_urls = _sync_urls
_base_run_full_sync = base.run_full_sync


def run_full_sync(job_id):
    """Run one manual full sync only when no other catalog writer is active."""
    with catalog_sync_lock() as acquired:
        if not acquired:
            return base.write_job(job_id, {
                "status": "failed",
                "phase": "failed",
                "error": "another_sync_is_running",
                "message": "همگام‌سازی خودکار یا دستی دیگری در حال اجراست؛ این اجرا شروع نشد.",
            })
        return _base_run_full_sync(job_id)
