import os
import time
from decimal import Decimal

from django.utils import timezone

from shop.models import Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services import source_catalog as legacy
from shop.services import source_sync
from shop.services.category_v21 import canonical_path, enhanced_category_names, sync_category_path
from shop.services.source_identity_v21 import (
    aggregate_product,
    find_canonical_product,
    find_offer,
    identity_key,
)
from shop.services.source_sync import SourceNotProductError, SourcePriceUnavailableError, SourceSyncError


# Make every v21 scrape use the broader category parser. AppConfig installs the
# old generic parser first; this module is loaded later by the API/job chain.
source_sync._extract_category_names = enhanced_category_names

CatalogSkip = legacy.CatalogSkip
source_products = legacy.source_products
discover_product_urls = legacy.discover_product_urls
apply_site_markup_to_existing = legacy.apply_site_markup_to_existing


def _retryable(exc):
    if isinstance(exc, (SourceNotProductError, SourcePriceUnavailableError)):
        return False
    if not isinstance(exc, SourceSyncError):
        return False
    text = str(exc).casefold()
    return any(marker in text for marker in (
        "ارتباط با سایت منبع برقرار نشد", "timeout", "timed out", "connection",
        "max retries", "temporarily unavailable", "connection reset", "http 408",
        "http 425", "http 429", "http 500", "http 502", "http 503", "http 504",
    ))


def scrape_product_with_retry(url, attempts=None):
    attempts = attempts if attempts is not None else int(os.getenv("DELTA_SOURCE_FETCH_RETRIES", "2"))
    attempts = max(1, min(int(attempts), 4))
    delay = max(0.0, min(float(os.getenv("DELTA_SOURCE_FETCH_RETRY_DELAY", "0.6")), 3.0))
    last = None
    for attempt in range(1, attempts + 1):
        try:
            data = source_sync.scrape_product(url)
            data["categories"] = canonical_path(data.get("categories") or [], data.get("name") or "", data.get("specs") or {})
            return data
        except Exception as exc:
            last = exc
            if attempt >= attempts or not _retryable(exc):
                raise
            if delay:
                time.sleep(delay * (2 ** (attempt - 1)))
    raise last


def _markup_price(source_price, markup_type, markup_value):
    base = Decimal(source_price or 0)
    value = Decimal(markup_value or 0)
    if markup_type == SourceSite.MARKUP_PERCENT:
        result = base * (Decimal("1") + value / Decimal("100"))
    else:
        result = base + value
    return max(0, int(result.quantize(Decimal("1"))))


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
        "active": bool(product.is_active),
    }


def _legacy_product(site, url, data):
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


def _payload(data):
    return {
        "name": str(data.get("name") or "")[:300],
        "description": str(data.get("description") or "")[:12000],
        "image_url": str(data.get("image_url") or "")[:4096],
        "gallery": list(data.get("gallery") or [])[:12],
        "specs": dict(data.get("specs") or {}),
    }


def _choose_product(site, canonical_url, fallback_url, data, model_key):
    source_code = str(data.get("sku") or "").strip()[:120]
    offer = find_offer(site, canonical_url, source_code, model_key)
    if not offer and fallback_url != canonical_url:
        offer = find_offer(site, fallback_url, source_code, model_key)
    if offer:
        return offer.product, offer, False

    product = find_canonical_product(model_key)
    if product:
        return product, None, False

    product = _legacy_product(site, canonical_url, data) or _legacy_product(site, fallback_url, data)
    if product:
        return product, None, False
    return None, None, True


def _upsert_with_data(site, url, data, allow_unpriced=False):
    canonical_url = str(data.get("source_url") or url)
    source_code = str(data.get("sku") or "").strip()[:120]
    model_key = identity_key(data)
    product, offer, product_created = _choose_product(site, canonical_url, url, data, model_key)
    incoming_price = max(0, int(data.get("price") or 0))

    if product_created and not incoming_price and not allow_unpriced:
        raise CatalogSkip("محصول جدید فعلاً بدون قیمت است؛ در Sync بعدی دوباره بررسی می‌شود.")

    category_path = canonical_path(data.get("categories") or [], data.get("name") or "", data.get("specs") or {})
    category = sync_category_path(category_path)
    sale_price = _markup_price(incoming_price, site.default_markup_type, site.default_markup_value) if incoming_price else 0

    if product is None:
        sku = source_code or None
        if sku and Product.objects.filter(sku=sku).exists():
            sku = None
        product = Product.objects.create(
            category=category,
            name=str(data.get("name") or "محصول")[:300],
            description=data.get("description") or "",
            source_type=Product.SYNCED,
            source_url=canonical_url,
            source_product_code=source_code,
            source_price=incoming_price,
            price=sale_price,
            stock=max(0, int(data.get("stock") or 0)),
            image_url=data.get("image_url") or "",
            gallery=data.get("gallery") or [],
            specs=data.get("specs") or {},
            sku=sku,
            markup_type=site.default_markup_type,
            markup_value=site.default_markup_value,
            last_synced_at=timezone.now(),
            sync_error="" if incoming_price else "قیمت منبع فعلاً قابل استخراج نیست.",
        )
        product_created = True

    if offer is None:
        # A redirect may have changed a legacy URL. Reuse the source/model offer
        # where possible, otherwise create one source record for this canonical product.
        offer = find_offer(site, canonical_url, source_code, model_key)
    values = {
        "product": product,
        "source_site": site,
        "source_product_code": source_code,
        "model_key": model_key,
        "source_price": incoming_price,
        "sale_price": sale_price,
        "stock": max(0, int(data.get("stock") or 0)),
        "category_path": category_path,
        "payload": _payload(data),
        "is_active": True,
        "last_seen_at": timezone.now(),
    }
    if offer:
        for field, value in values.items():
            setattr(offer, field, value)
        if offer.source_url != canonical_url and not ProductSourceOffer.objects.exclude(pk=offer.pk).filter(source_url=canonical_url).exists():
            offer.source_url = canonical_url
        offer.save()
    else:
        offer = ProductSourceOffer.objects.create(source_url=canonical_url, **values)

    product = aggregate_product(product)
    if not incoming_price:
        Product.objects.filter(pk=product.pk).update(sync_error="قیمت این منبع فعلاً قابل استخراج نیست؛ موجودی سایر منابع حفظ شد.")
        product.refresh_from_db()
    return product, product_created


def upsert_source_product(site, url):
    return _upsert_with_data(site, url, scrape_product_with_retry(url), allow_unpriced=False)


def upsert_source_product_with_changes(site, url):
    data = scrape_product_with_retry(url)
    canonical_url = str(data.get("source_url") or url)
    source_code = str(data.get("sku") or "").strip()[:120]
    model_key = identity_key(data)
    offer = find_offer(site, canonical_url, source_code, model_key) or find_offer(site, url, source_code, model_key)
    existing = offer.product if offer else find_canonical_product(model_key) or _legacy_product(site, canonical_url, data) or _legacy_product(site, url, data)
    before = _snapshot(existing)
    product, created = _upsert_with_data(site, canonical_url, data, allow_unpriced=False)
    after = _snapshot(product)
    changes = {}
    if created:
        changes["new"] = True
    elif before:
        for field in ("name", "source_price", "price", "stock", "image_url", "gallery", "specs", "category_id", "active"):
            if before.get(field) != after.get(field):
                changes[field] = (before.get(field), after.get(field))
    return product, created, changes


def import_unpriced_catalog_product(site, url):
    data = scrape_product_with_retry(url)
    return _upsert_with_data(site, url, data, allow_unpriced=True)
