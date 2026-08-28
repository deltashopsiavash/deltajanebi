"""Product identity and duplicate repair for Delta catalog v22.

v22 keeps the useful v21 model parser, but treats capacity/size variants as
separate identities, allows multiple URLs from one source to contribute stock
to one canonical product, repairs products that were previously over-merged,
and actually removes duplicate Product rows after moving their references.
"""
import re
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from shop.models import Product
from shop.source_offer_models import ProductSourceOffer
from shop.services import source_identity_v21 as v21
from shop.services import category_v22


def _variant_text(data):
    specs = data.get("specs") or {}
    pieces = [str(data.get("name") or "")]
    pieces.extend(f"{key} {value}" for key, value in specs.items())
    return v21._norm(" ".join(pieces))


def _spec_capacity(data):
    specs = data.get("specs") or {}
    for raw_key, raw_value in specs.items():
        compact = v21._compact(raw_key)
        if any(token in compact for token in ("ظرفیت", "حافظه", "capacity", "storage", "memory")):
            text = v21._norm(raw_value)
            match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(tb|gb|ترابایت|گیگ(?:ابایت)?|g)(?![a-z])", text, re.I)
            if match:
                value = match.group(1).rstrip("0").rstrip(".") if "." in match.group(1) else match.group(1)
                unit = match.group(2).casefold()
                unit = "tb" if unit in {"tb", "ترابایت"} else "gb"
                return f"storage:{value}{unit}"
            bare = re.fullmatch(r"\s*(\d{1,5})\s*", text)
            if bare:
                return f"storage:{bare.group(1)}gb"
    return ""


def variant_key(data):
    """Return variants that must stay separate despite an equal model code."""
    explicit = _spec_capacity(data)
    if explicit:
        return explicit

    text = _variant_text(data)
    storage = re.search(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(tb|gb|ترابایت|گیگ(?:ابایت)?|g)(?![a-z])",
        text,
        re.I,
    )
    if storage:
        value = storage.group(1).rstrip("0").rstrip(".") if "." in storage.group(1) else storage.group(1)
        unit = storage.group(2).casefold()
        unit = "tb" if unit in {"tb", "ترابایت"} else "gb"
        return f"storage:{value}{unit}"

    battery = re.search(
        r"(?<!\d)(\d{3,6})\s*(?:mah|m\s*ah|میلی\s*آمپر(?:\s*ساعت)?|میلی‌آمپر(?:\s*ساعت)?)",
        text,
        re.I,
    )
    if battery:
        return f"battery:{battery.group(1)}mah"

    if "کابل" in text or " cable" in f" {text}":
        length = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(متر|meter|metre|m\b|سانتی\s*متر|cm\b)", text, re.I)
        if length:
            value = length.group(1).rstrip("0").rstrip(".") if "." in length.group(1) else length.group(1)
            unit = length.group(2).casefold().replace(" ", "")
            unit = "cm" if unit in {"cm", "سانتیمتر"} else "m"
            return f"length:{value}{unit}"
    return ""


def identity_key(data):
    base = v21.extract_model_key(data)
    if base:
        variant = variant_key(data)
        return f"{base}|{variant}" if variant else base
    return v21.exact_name_key(data)


def _strong_identity(value):
    value = str(value or "")
    return value.startswith("model:") or (value.startswith("name:") and len(value) >= 22)


def find_offer(site, url, source_code="", model_key=""):
    """Match one source offer by URL only, allowing same-model URL aggregation."""
    return ProductSourceOffer.objects.filter(source_url=url).select_related("product").first()


def find_canonical_product(model_key):
    if not _strong_identity(model_key):
        return None
    offer = (
        ProductSourceOffer.objects.filter(
            model_key=model_key,
            product__source_type=Product.SYNCED,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("product_id", "id")
        .first()
    )
    return offer.product if offer else None


def _offer_identity(offer):
    payload = dict(offer.payload or {})
    if not payload.get("name"):
        payload["name"] = offer.product.name
    if not payload.get("specs"):
        payload["specs"] = offer.product.specs or {}
    return identity_key(payload)


def refresh_offer_identity_keys():
    changed = 0
    rows = ProductSourceOffer.objects.select_related("product").all().order_by("id")
    for offer in rows.iterator(chunk_size=200):
        value = _offer_identity(offer)
        if value != (offer.model_key or ""):
            ProductSourceOffer.objects.filter(pk=offer.pk).update(model_key=value)
            changed += 1
    return changed


def _new_product_for_offer(source_product, representative):
    payload = representative.payload or {}
    category = category_v22.sync_category_path(representative.category_path or [])
    source_site = representative.source_site
    return Product.objects.create(
        category=category or source_product.category,
        name=str(payload.get("name") or source_product.name or "محصول")[:300],
        description=str(payload.get("description") or source_product.description or "")[:12000],
        source_type=Product.SYNCED,
        source_url=representative.source_url,
        source_product_code=representative.source_product_code or "",
        source_price=max(0, int(representative.source_price or 0)),
        price=max(0, int(representative.sale_price or source_product.price or 0)),
        stock=0,
        image_url=str(payload.get("image_url") or "")[:4096],
        gallery=list(payload.get("gallery") or []),
        specs=dict(payload.get("specs") or {}),
        sku=None,
        markup_type=(source_site.default_markup_type if source_site else source_product.markup_type),
        markup_value=(source_site.default_markup_value if source_site else source_product.markup_value),
        is_active=True,
    )


def aggregate_product(product):
    """Aggregate all active source offers with exact v22 category/image policy."""
    offers = list(
        ProductSourceOffer.objects.filter(product=product, is_active=True)
        .select_related("source_site")
        .order_by("id")
    )
    if not offers:
        return product

    total_stock = sum(max(0, int(x.stock or 0)) for x in offers)
    priced = [x for x in offers if int(x.sale_price or 0) > 0]
    in_stock = [x for x in priced if int(x.stock or 0) > 0]
    pool = in_stock or priced or offers
    best = min(
        pool,
        key=lambda x: (
            int(x.sale_price or 10**30) if int(x.sale_price or 0) else 10**30,
            x.id,
        ),
    )

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
    if payload.get("specs"):
        updates["specs"] = payload["specs"]

    if not product.manual_image_url_override:
        preferred = sorted(offers, key=lambda x: (0 if x.pk == best.pk else 1, x.id))
        clean_image = None
        clean_gallery = None
        rejected_seen = False
        for offer in preferred:
            item = offer.payload or {}
            rejected_seen = rejected_seen or bool(item.get("image_rejected"))
            image = str(item.get("image_url") or "").strip()
            if image and not item.get("image_rejected"):
                clean_image = image
                clean_gallery = list(item.get("gallery") or []) or [image]
                break
        if clean_image:
            updates["image_url"] = clean_image
            updates["gallery"] = clean_gallery
        elif rejected_seen:
            # A missing photo is safer than retaining an old source advertisement.
            updates["image_url"] = ""
            updates["gallery"] = []

    paths = [list(x.category_path or []) for x in offers if x.category_path]
    if paths:
        chosen = max(paths, key=lambda x: (len(x), sum(len(str(p)) for p in x)))
        category = category_v22.sync_category_path(chosen)
        if category:
            updates["category_id"] = category.id

    Product.objects.filter(pk=product.pk).update(**updates)
    return Product.objects.select_related("category").get(pk=product.pk)


@transaction.atomic
def split_mixed_identity_products():
    """Undo historical over-merges such as one model combining 16GB and 128GB."""
    stats = {"products_split": 0, "offers_reassigned": 0}
    product_ids = list(ProductSourceOffer.objects.values_list("product_id", flat=True).distinct().order_by("product_id"))
    for product_id in product_ids:
        try:
            product = Product.objects.get(pk=product_id, source_type=Product.SYNCED)
        except Product.DoesNotExist:
            continue
        offers = list(ProductSourceOffer.objects.filter(product=product).select_related("source_site").order_by("id"))
        model_groups = defaultdict(list)
        for offer in offers:
            offer_identity = offer.model_key or _offer_identity(offer)
            if offer_identity.startswith("model:") and _strong_identity(offer_identity):
                model_groups[offer_identity].append(offer)
        if len(model_groups) <= 1:
            continue

        keep_identity = max(
            model_groups,
            key=lambda value: (
                len(model_groups[value]),
                sum(max(0, int(x.stock or 0)) for x in model_groups[value]),
                -min(x.id for x in model_groups[value]),
            ),
        )
        for offer_identity, group in model_groups.items():
            if offer_identity == keep_identity:
                continue
            target = _new_product_for_offer(product, group[0])
            moved = ProductSourceOffer.objects.filter(pk__in=[x.pk for x in group]).update(product=target)
            stats["offers_reassigned"] += moved
            stats["products_split"] += 1
            aggregate_product(target)
        aggregate_product(product)
    return stats


def _transfer_amazing_offer(duplicate, canonical):
    try:
        from enhancements.models import ProductAmazing
    except Exception:
        return
    source = ProductAmazing.objects.filter(product=duplicate).first()
    if not source:
        return
    target = ProductAmazing.objects.filter(product=canonical).first()
    if target is None:
        source.product = canonical
        source.save(update_fields=["product"])
        return
    source_price = int(source.price or 0)
    target_price = int(target.price or 0)
    source_better = bool(source.is_active) and source_price > 0 and (
        not target.is_active or target_price <= 0 or source_price < target_price
    )
    if source_better:
        target.price = source.price
        target.is_active = source.is_active
        target.expires_at = source.expires_at
        target.save(update_fields=["price", "is_active", "expires_at"])
    source.delete()


@transaction.atomic
def consolidate_duplicate_products():
    """Collapse exact v22 identities and remove duplicate Product rows safely."""
    refreshed = refresh_offer_identity_keys()
    split = split_mixed_identity_products()
    stats = {
        "products_merged": 0,
        "products_deleted": 0,
        "offers_moved": 0,
        "identity_refreshed": refreshed,
        **split,
    }

    groups = defaultdict(set)
    for offer in ProductSourceOffer.objects.exclude(model_key="").only("product_id", "model_key"):
        if _strong_identity(offer.model_key):
            groups[offer.model_key].add(offer.product_id)

    for offer_identity, product_ids in groups.items():
        if len(product_ids) < 2:
            continue
        products = list(Product.objects.filter(pk__in=product_ids, source_type=Product.SYNCED))
        if len(products) < 2:
            continue
        canonical = max(products, key=v21._canonical_score)
        for duplicate in products:
            if duplicate.pk == canonical.pk or not Product.objects.filter(pk=duplicate.pk).exists():
                continue
            v21._copy_manual_overrides(duplicate, canonical)

            reserved = max(0, int(canonical.reserved_stock or 0)) + max(0, int(duplicate.reserved_stock or 0))
            if reserved != int(canonical.reserved_stock or 0):
                Product.objects.filter(pk=canonical.pk).update(reserved_stock=reserved)
                canonical.reserved_stock = reserved
            try:
                from shop.models import OrderItem
                OrderItem.objects.filter(product=duplicate).update(product=canonical)
            except Exception:
                pass
            _transfer_amazing_offer(duplicate, canonical)

            moved = ProductSourceOffer.objects.filter(product=duplicate).update(product=canonical)
            stats["offers_moved"] += moved
            duplicate.delete()
            stats["products_merged"] += 1
            stats["products_deleted"] += 1
        aggregate_product(canonical)
    return stats
