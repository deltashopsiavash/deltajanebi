import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from shop.models import Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services.category_v21 import canonical_path, sync_category_path


_TRANSLATION = str.maketrans({
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "ي": "ی", "ى": "ی", "ك": "ک",
})
_MODEL_KEYS = {
    "مدل", "مدلکالا", "مدلمحصول", "کدمدل", "شماره مدل", "شمارهمدل",
    "model", "modelno", "modelnumber", "partnumber", "mpn",
}
_STOP_NAME = {
    "خرید", "فروش", "قیمت", "محصول", "کالا", "مدل", "اصل", "اورجینال",
    "original", "new", "جدید", "با", "برای", "و", "the", "a", "an",
}


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION).casefold()
    text = text.replace("\u200c", " ")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value):
    return re.sub(r"[^a-z0-9آ-ی]+", "", _norm(value))


def _candidate_model(value):
    raw = _norm(value)
    if not raw:
        return ""
    # Prefer a compact mixed alpha-numeric code. Models such as EP-T2510,
    # JR-T03, A15 and X200 normalize to the same key across source sites.
    pieces = re.findall(r"[a-z0-9]+(?:[-_/ ][a-z0-9]+){0,3}", raw)
    candidates = []
    for piece in pieces:
        compact = re.sub(r"[^a-z0-9]", "", piece)
        if len(compact) < 2 or len(compact) > 40:
            continue
        if not re.search(r"[a-z]", compact) or not re.search(r"\d", compact):
            continue
        if re.fullmatch(r"\d+(?:w|v|a|mah|wh|gb|tb|hz|cm|mm|m)", compact):
            continue
        if compact in {"usb20", "usb30", "usb31", "usb32", "typec", "wifi6", "bt50", "bt51", "bt52", "bt53"}:
            continue
        score = len(compact) + (4 if re.search(r"[a-z]{2,}", compact) else 0)
        candidates.append((score, compact))
    return max(candidates)[1] if candidates else ""


def extract_model_key(data):
    specs = data.get("specs") or {}
    for raw_key, raw_value in specs.items():
        k = _compact(raw_key)
        if k in {_compact(x) for x in _MODEL_KEYS}:
            candidate = _candidate_model(raw_value)
            if candidate:
                return f"model:{candidate}"

    name = _norm(data.get("name"))
    for match in re.finditer(r"(?:مدل|model)\s*[:：\-]?\s*([^،,|()\[\]\n]{2,50})", name, re.I):
        candidate = _candidate_model(match.group(1))
        if candidate:
            return f"model:{candidate}"

    # Scan title tokens, preferring longer distinctive model codes. Generic
    # capacities such as 25W/10000mAh are rejected above.
    candidate = _candidate_model(name)
    if candidate:
        return f"model:{candidate}"

    return ""


def exact_name_key(data):
    text = _norm(data.get("name"))
    words = []
    for token in re.findall(r"[a-z0-9آ-ی]+", text):
        if token in _STOP_NAME or len(token) < 2:
            continue
        words.append(token)
    # Name matching is intentionally strict; it is only a fallback when no
    # model code is available, avoiding false merges of generic accessories.
    return "name:" + "".join(words) if len(words) >= 3 else ""


def identity_key(data):
    return extract_model_key(data) or exact_name_key(data)


def _source_for_url(url):
    host = (urlparse(str(url or "")).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return SourceSite.objects.filter(hostname=host).first()


def _payload_from_product(product):
    return {
        "name": product.name,
        "description": product.description,
        "image_url": product.image_url,
        "gallery": product.gallery or [],
        "specs": product.specs or {},
    }


def _category_path_for_product(product):
    if not product.category_id:
        return []
    return [item.name for item in product.category.ancestor_chain()]


def backfill_existing_offers():
    """Turn legacy one-source Product rows into v21 offer rows lazily."""
    created = 0
    rows = Product.objects.filter(source_type=Product.SYNCED).exclude(source_url="").select_related("category")
    for product in rows.iterator(chunk_size=200):
        if ProductSourceOffer.objects.filter(source_url=product.source_url).exists():
            continue
        data = {"name": product.name, "specs": product.specs or {}}
        site = _source_for_url(product.source_url)
        try:
            ProductSourceOffer.objects.create(
                product=product,
                source_site=site,
                source_url=product.source_url,
                source_product_code=product.source_product_code or product.sku or "",
                model_key=identity_key(data),
                source_price=int(product.source_price or 0),
                sale_price=int(product.price or 0),
                stock=int(product.stock or 0),
                category_path=_category_path_for_product(product),
                payload=_payload_from_product(product),
                is_active=True,
                last_seen_at=product.last_synced_at or timezone.now(),
            )
            created += 1
        except Exception:
            # A duplicate canonical URL may already have been backfilled by a
            # concurrent job; leave it to the next consolidation pass.
            pass
    return created


def _canonical_score(product):
    manual = int(bool(product.manual_name_override or product.manual_image_url_override or product.manual_price_override is not None or product.manual_stock_override is not None))
    offers = ProductSourceOffer.objects.filter(product=product, is_active=True).count()
    return (manual, int(product.is_active), offers, -int(product.pk))


def _copy_manual_overrides(source, target):
    changed = []
    for field in ("manual_name_override", "manual_image_url_override", "manual_price_override", "manual_stock_override"):
        if getattr(target, field) in (None, "") and getattr(source, field) not in (None, ""):
            setattr(target, field, getattr(source, field))
            changed.append(field)
    if changed:
        target.save(update_fields=changed)


def aggregate_product(product):
    offers = list(ProductSourceOffer.objects.filter(product=product, is_active=True).select_related("source_site"))
    if not offers:
        return product

    total_stock = sum(max(0, int(x.stock or 0)) for x in offers)
    priced = [x for x in offers if int(x.sale_price or 0) > 0]
    in_stock = [x for x in priced if int(x.stock or 0) > 0]
    pool = in_stock or priced or offers
    best = min(pool, key=lambda x: (int(x.sale_price or 10**30) if int(x.sale_price or 0) else 10**30, x.id))

    updates = {
        "source_url": best.source_url,
        "source_product_code": best.source_product_code,
        "last_synced_at": max((x.last_seen_at for x in offers), default=timezone.now()),
        "sync_error": "",
        "is_active": True,
    }
    if product.manual_stock_override is None:
        updates["stock"] = total_stock
    if product.manual_price_override is None and int(best.sale_price or 0) > 0:
        updates["source_price"] = int(best.source_price or 0)
        updates["price"] = int(best.sale_price or 0)
        if best.source_site:
            updates["markup_type"] = best.source_site.default_markup_type
            updates["markup_value"] = best.source_site.default_markup_value

    payload = best.payload or {}
    if not product.manual_name_override and payload.get("name"):
        updates["name"] = str(payload["name"])[:300]
    if payload.get("description"):
        updates["description"] = payload["description"]
    if not product.manual_image_url_override and payload.get("image_url"):
        updates["image_url"] = payload["image_url"]
    if payload.get("gallery"):
        updates["gallery"] = payload["gallery"]
    if payload.get("specs"):
        updates["specs"] = payload["specs"]

    paths = [list(x.category_path or []) for x in offers if x.category_path]
    if paths:
        chosen = max(paths, key=lambda x: (len(x), sum(len(str(p)) for p in x)))
        category = sync_category_path(chosen)
        if category:
            updates["category_id"] = category.id

    # QuerySet.update intentionally bypasses Product.save() so source stock can
    # be aggregated without accidentally converting it into a manual override.
    Product.objects.filter(pk=product.pk).update(**updates)
    return Product.objects.select_related("category").get(pk=product.pk)


@transaction.atomic
def consolidate_duplicate_products():
    """Collapse duplicate synced rows by strong model identity.

    Historical duplicate Product rows are kept inactive instead of deleted so
    old OrderItems remain valid. Their source offers are moved to the canonical
    product and live stock is aggregated across all sources.
    """
    stats = {"products_merged": 0, "offers_moved": 0}
    groups = defaultdict(set)
    for offer in ProductSourceOffer.objects.exclude(model_key="").only("product_id", "model_key"):
        if offer.model_key.startswith("model:"):
            groups[offer.model_key].add(offer.product_id)

    for model_key, product_ids in groups.items():
        if len(product_ids) < 2:
            continue
        products = list(Product.objects.filter(pk__in=product_ids, source_type=Product.SYNCED))
        if len(products) < 2:
            continue
        canonical = max(products, key=_canonical_score)
        for duplicate in products:
            if duplicate.pk == canonical.pk:
                continue
            _copy_manual_overrides(duplicate, canonical)
            moved = ProductSourceOffer.objects.filter(product=duplicate).update(product=canonical)
            stats["offers_moved"] += moved
            Product.objects.filter(pk=duplicate.pk).update(
                is_active=False,
                stock=0,
                sync_error=f"merged_into:{canonical.public_code or canonical.pk}; model={model_key[:120]}",
            )
            stats["products_merged"] += 1
        canonical = aggregate_product(canonical)
    return stats


def find_offer(site, url, source_code, model_key):
    offer = ProductSourceOffer.objects.filter(source_url=url).select_related("product").first()
    if offer:
        return offer
    if source_code:
        offer = ProductSourceOffer.objects.filter(source_site=site, source_product_code=source_code).select_related("product").first()
        if offer:
            return offer
    if model_key:
        offer = ProductSourceOffer.objects.filter(source_site=site, model_key=model_key).select_related("product").first()
        if offer:
            return offer
    return None


def find_canonical_product(model_key):
    if not model_key or not model_key.startswith("model:"):
        return None
    offer = (
        ProductSourceOffer.objects.filter(model_key=model_key, product__source_type=Product.SYNCED, product__is_active=True)
        .select_related("product")
        .order_by("product_id", "id")
        .first()
    )
    return offer.product if offer else None
