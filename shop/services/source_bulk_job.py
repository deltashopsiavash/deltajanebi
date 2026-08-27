import fcntl
import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.utils import timezone

from shop.models import Product, SourceSite
from shop.services import source_sync
from shop.services.source_catalog import (
    CatalogSkip,
    discover_product_urls,
    source_products,
    upsert_source_product_with_changes,
)
from shop.services.source_sync import SourceNotProductError, sync_category_path

JOB_DIR = Path(os.environ.get("DELTA_SOURCE_SYNC_JOB_DIR", "/tmp/deltajanebi-source-sync-jobs"))
LOCK_FILE = JOB_DIR / "active.lock"
MAX_WARNINGS = 20


def _now():
    return datetime.now(dt_timezone.utc).isoformat()


def _job_path(job_id):
    safe = "".join(ch for ch in str(job_id or "") if ch.isalnum() or ch in "-_")
    if not safe:
        raise ValueError("invalid_job_id")
    return JOB_DIR / f"{safe}.json"


def write_job(job_id, payload):
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    path = _job_path(job_id)
    data = dict(payload or {})
    data["job_id"] = str(job_id)
    data["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return data


def read_job(job_id):
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def create_queued_job(job_id):
    return write_job(job_id, {
        "status": "queued",
        "sites": 0,
        "total": 0,
        "checked": 0,
        "created": 0,
        "changed": 0,
        "skipped": 0,
        "errors": 0,
        "current_site": "",
        "warnings": [],
    })


def _existing_urls(site):
    return list(source_products(site).exclude(source_url="").values_list("source_url", flat=True))


def _import_unpriced_catalog_product(site, url):
    """Keep a real source product in the catalog even when its price is hidden.

    It is saved with price/stock zero so it cannot be bought for free. A later
    normal sync fills price and stock as soon as the source exposes them.
    """
    data = source_sync.scrape_product(url)
    canonical_url = data.get("source_url") or url
    existing = Product.objects.filter(source_type=Product.SYNCED, source_url=canonical_url).first()
    if existing:
        return existing, False

    category = sync_category_path(data.get("categories") or [])
    sku = str(data.get("sku") or "").strip() or None
    if sku and Product.objects.filter(sku=sku).exists():
        sku = None
    product = Product.objects.create(
        category=category,
        name=data["name"],
        description=data.get("description", ""),
        source_type=Product.SYNCED,
        source_url=canonical_url,
        source_product_code=str(data.get("sku") or "")[:100],
        source_price=0,
        price=0,
        stock=0,
        image_url=data.get("image_url", ""),
        gallery=data.get("gallery") or [],
        specs=data.get("specs") or {},
        sku=sku,
        markup_type=site.default_markup_type,
        markup_value=site.default_markup_value,
        last_synced_at=timezone.now(),
        sync_error="قیمت منبع فعلاً قابل استخراج نیست؛ در همگام‌سازی بعدی دوباره بررسی می‌شود.",
    )
    return product, True


def run_full_sync(job_id):
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("a+")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return write_job(job_id, {
                "status": "failed",
                "error": "another_sync_is_running",
                "message": "یک همگام‌سازی دیگر در حال اجراست.",
            })

        sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))
        state = {
            "status": "running",
            "sites": len(sites),
            "total": 0,
            "checked": 0,
            "created": 0,
            "changed": 0,
            "skipped": 0,
            "errors": 0,
            "current_site": "",
            "warnings": [],
        }
        write_job(job_id, state)

        plans = []
        for site in sites:
            state["current_site"] = site.name
            write_job(job_id, state)
            try:
                if site.bulk_import_enabled:
                    discovered = discover_product_urls(site)
                    site.last_discovered_count = len(discovered)
                    site.save(update_fields=["last_discovered_count"])
                    urls = list(dict.fromkeys([*discovered, *_existing_urls(site)]))
                    if not discovered and urls and len(state["warnings"]) < MAX_WARNINGS:
                        state["warnings"].append(f"{site.name}: محصول جدیدی کشف نشد؛ محصولات قبلی همگام شدند.")
                else:
                    urls = list(dict.fromkeys(_existing_urls(site)))
            except Exception as exc:
                urls = list(dict.fromkeys(_existing_urls(site)))
                if len(state["warnings"]) < MAX_WARNINGS:
                    state["warnings"].append(f"{site.name}: discovery: {str(exc)[:180]}")
            plans.append((site, urls))
            state["total"] += len(urls)
            write_job(job_id, state)

        if not state["total"]:
            state["status"] = "completed"
            state["current_site"] = ""
            return write_job(job_id, state)

        for site, urls in plans:
            state["current_site"] = site.name
            for url in urls:
                try:
                    _, created, changes = upsert_source_product_with_changes(site, url)
                    if created:
                        state["created"] += 1
                    if created or changes:
                        state["changed"] += 1
                except CatalogSkip as exc:
                    # Upload-all means the complete real catalog. If a valid
                    # product hides its price, retain it safely at stock/price 0
                    # instead of silently dropping it from Delta.
                    try:
                        _, created = _import_unpriced_catalog_product(site, url)
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

            if site.bulk_import_enabled:
                site.last_bulk_sync_at = timezone.now()
                site.save(update_fields=["last_bulk_sync_at"])

        state["status"] = "completed"
        state["current_site"] = ""
        return write_job(job_id, state)
    except Exception as exc:
        current = read_job(job_id) or {}
        current.update({
            "status": "failed",
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
