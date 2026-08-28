"""Full catalog sync v22.

Keeps v21's detached/incremental job behavior, upgrades its category/identity
policies, raises discovery limits for complete catalogs, and exposes per-phase
counters so Telegram progress never appears frozen during discovery.
"""
import os
import time

# Import the v22 catalog bridge before v21 bulk so all legacy call sites have the
# same identity/category behavior in this process.
from shop.services import source_catalog_v22 as catalog
from shop.services import source_bulk_job_v21 as base
from shop.services import category_v22
from shop.services import source_identity_v22 as identity
from shop.services.source_sync import SourceNotProductError

base.upsert_source_product_with_changes = catalog.upsert_source_product_with_changes
base.import_unpriced_catalog_product = catalog.import_unpriced_catalog_product
base.CatalogSkip = catalog.CatalogSkip
base.consolidate_sibling_duplicates = category_v22.consolidate_sibling_duplicates
base.consolidate_duplicate_products = identity.consolidate_duplicate_products
base.aggregate_product = identity.aggregate_product

# v21's 75-second/220-page discovery guard was intentionally conservative but
# can stop large source catalogs early. v22 still has a hard bound, but gives
# sitemap/category crawling enough room to enumerate a normal wholesale store.
base.DISCOVERY_BUDGET = max(120, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "600")))
base.DISCOVERY_MAX_SITEMAPS = max(100, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "1000")))
base.DISCOVERY_MAX_PAGES = max(100, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "1500")))
base.PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.02")), 1.0))


def _sync_urls(job_id, state, site, urls, phase):
    urls = list(urls or [])
    state["phase"] = phase
    state["phase_total"] = len(urls)
    state["phase_checked"] = 0
    state["current_site"] = site.name
    base.write_job(job_id, state)

    for url in urls:
        state["current_url"] = str(url)[:500]
        base.write_job(job_id, state)
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
            except Exception as nested:
                state["errors"] += 1
                base._warn(state, f"{site.name}: import-unpriced: {nested}")
        except SourceNotProductError as exc:
            base._mark_source_missing(site, url)
            state["skipped"] += 1
            base._warn(state, f"{site.name}: {exc}")
        except Exception as exc:
            state["errors"] += 1
            base._warn(state, f"{site.name}: {exc}")

        state["checked"] += 1
        state["phase_checked"] += 1
        base.write_job(job_id, state)
        if base.PRODUCT_DELAY:
            time.sleep(base.PRODUCT_DELAY)


base._sync_urls = _sync_urls
run_full_sync = base.run_full_sync
