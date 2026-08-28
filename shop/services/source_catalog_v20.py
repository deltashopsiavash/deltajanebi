import os
import time
from decimal import Decimal

from django.utils import timezone

from shop.models import Product, SourceSite
from shop.services import source_catalog as legacy
from shop.services import source_sync
from shop.services.category_normalizer import sync_category_path
from shop.services.source_sync import (
    SourceNotProductError,
    SourcePriceUnavailableError,
    SourceSyncError,
)

CatalogSkip = legacy.CatalogSkip
source_products = legacy.source_products
discover_product_urls = legacy.discover_product_urls
apply_site_markup_to_existing = legacy.apply_site_markup_to_existing


def _retryable_source_error(exc):
    if isinstance(exc, (SourceNotProductError, SourcePriceUnavailableError)):
        return False
    if not isinstance(exc, SourceSyncError):
        return False
    text = str(exc).casefold()
    markers = (
        "ارتباط با سایت منبع برقرار نشد",
        "timeout",
        "timed out",
        "max retries",
        "connection",
        "remote disconnected",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "http 408",
        "http 425",
        "http 429",
        "http 500",
        "http 502",
        "http 503",
        "http 504",
    )
    return any(marker in text for marker in markers)


def scrape_product_with_retry(url, attempts=None):
    """Fetch a source product with bounded exponential retry for network faults."""
    if attempts is None:
        attempts = int(os.getenv("DELTA_SOURCE_FETCH_RETRIES", "3"))
    attempts = max(1, min(int(attempts), 5))
    base_delay = max(0.0, min(float(os.getenv("DELTA_SOURCE_FETCH_RETRY_DELAY", "0.8")), 5.0))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return source_sync.scrape_product(url)
        except Exception as exc:
            last = exc
            if attempt >= attempts or not _retryable_source_error(exc):
                raise
            if base_delay:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    raise last


def _markup_price(source_price, markup_type, markup_value):
    base = Decimal(source_price or 0)
    value = Decimal(markup_value or 0)
    if markup_type == SourceSite.MARKUP_PERCENT:
        result = base * (Decimal("1") + value / Decimal("100"))
    else:
        result = base + value
    return max(0, int(result.quantize(Decimal("1"))))


def _existing_for_source(site, url, data):
    product = Product.objects.filter(source_type=Product.SYNCED, source_url=url).first()
    if product:
        return product
    source_code = str(data.get("sku") or "").strip()
    if source_code:
        return Product.objects.filter(
            source_type=Product.SYNCED,
            source_product_code=source_code,
            source_url__icontains=site.hostname,
        ).first()
    return None


def _snapshot(product):
    if not product:
        return None
    return {
        "name": product.name,
        "source_price": int(product.source_price or 0),
        "price": int(product.price or 0),
        "stock": int(product.stock or 0),
        "image_url": product.image_url or "",
        "gallery": tuple(product.gallery or []),
        "specs": dict(product.specs or {}),
        "category_id": product.category_id,
    }


def _upsert_with_data(site, url, data):
    canonical_url = data.get("source_url") or url
    product = _existing_for_source(site, canonical_url, data) or _existing_for_source(site, url, data)
    created = product is None

    incoming_price = int(data.get("price") or 0)
    if created and not incoming_price:
        raise CatalogSkip("محصول جدید فعلاً ناموجود و بدون قیمت قابل استخراج است؛ تا نمایش قیمت از ورود خودکار رد شد.")

    # Always resolve the category. Previous versions did this only for newly
    # created products, which permanently left older products uncategorized or
    # attached to stale duplicate categories.
    category = sync_category_path(data.get("categories") or [])
    sku = str(data.get("sku") or "").strip() or None
    if sku and Product.objects.exclude(pk=product.pk if product else None).filter(sku=sku).exists():
        sku = None

    if created:
        product = Product(
            category=category,
            name=data["name"],
            description=data.get("description", ""),
            source_type=Product.SYNCED,
            source_url=canonical_url,
            source_product_code=str(data.get("sku") or "")[:100],
            source_price=incoming_price,
            stock=data.get("stock", 0),
            image_url=data.get("image_url", ""),
            gallery=data.get("gallery") or [],
            specs=data.get("specs") or {},
            sku=sku,
            markup_type=site.default_markup_type,
            markup_value=site.default_markup_value,
        )
    else:
        product.name = data.get("name") or product.name
        product.description = data.get("description") or product.description
        product.source_url = canonical_url
        product.source_product_code = str(data.get("sku") or product.source_product_code or "")[:100]
        if incoming_price:
            product.source_price = incoming_price
        product.stock = data.get("stock", 0)
        product.image_url = data.get("image_url") or product.image_url
        product.gallery = data.get("gallery") or product.gallery
        product.specs = data.get("specs") or product.specs
        if category:
            product.category = category
        if sku:
            product.sku = sku
        product.markup_type = site.default_markup_type
        product.markup_value = site.default_markup_value

    effective_source_price = incoming_price or int(product.source_price or 0)
    if effective_source_price:
        product.price = _markup_price(effective_source_price, site.default_markup_type, site.default_markup_value)
    product.last_synced_at = timezone.now()
    product.sync_error = ""
    product.save()
    return product, created


def upsert_source_product(site, url):
    return _upsert_with_data(site, url, scrape_product_with_retry(url))


def upsert_source_product_with_changes(site, url):
    data = scrape_product_with_retry(url)
    canonical_url = data.get("source_url") or url
    existing = _existing_for_source(site, canonical_url, data) or _existing_for_source(site, url, data)
    before = _snapshot(existing)
    product, created = _upsert_with_data(site, canonical_url, data)
    after = _snapshot(product)

    changes = {}
    if created:
        changes["new"] = True
    else:
        for key in ("name", "source_price", "price", "stock", "image_url", "gallery", "specs", "category_id"):
            if before.get(key) != after.get(key):
                changes[key] = (before.get(key), after.get(key))
    return product, created, changes


def import_unpriced_catalog_product(site, url):
    """Import a real no-price product safely while still using canonical categories."""
    data = scrape_product_with_retry(url)
    canonical_url = data.get("source_url") or url
    existing = Product.objects.filter(source_type=Product.SYNCED, source_url=canonical_url).first()
    if existing:
        category = sync_category_path(data.get("categories") or [])
        if category and existing.category_id != category.id:
            existing.category = category
            existing.save(update_fields=["category", "updated_at"])
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
