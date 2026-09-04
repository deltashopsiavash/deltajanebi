"""Robust source cleanup-term handling for Delta catalog v29.

User-entered cleanup terms are normalized (including Persian commas/newlines),
matched across Persian/Arabic character variants and ZWNJ/spacing differences,
and can be applied immediately to already-stored source offers/products without
waiting for another scrape.
"""
import re
import unicodedata

from django.db import transaction

from shop.models import Product
from shop.source_offer_models import ProductSourceOffer
from shop.services import source_sanitizer as legacy

_TRANSLATION = str.maketrans({
    "ي": "ی", "ى": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه",
    "ؤ": "و", "إ": "ا", "أ": "ا",
})
_SPLIT_RE = re.compile(r"[,،;؛|\n\r]+")
_SEP_PATTERN = r"[\s\u200c\u200f\ufeff\-_–—|/\\:.,،؛;()\[\]{}]*"
_CHAR_PATTERNS = {
    "ی": r"[یيى]",
    "ي": r"[یيى]",
    "ى": r"[یيى]",
    "ک": r"[کك]",
    "ك": r"[کك]",
    "ه": r"[هةۀ]",
    "ة": r"[هةۀ]",
    "ۀ": r"[هةۀ]",
    "ا": r"[اأإ]",
    "أ": r"[اأإ]",
    "إ": r"[اأإ]",
    "و": r"[وؤ]",
    "ؤ": r"[وؤ]",
}


def _normalize_text(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION)
    text = text.replace("\u200f", "").replace("\ufeff", "")
    return text


def normalize_terms(value):
    """Return a stable comma-separated term list from Persian/English input."""
    result = []
    seen = set()
    for raw in _SPLIT_RE.split(str(value or "")):
        item = _normalize_text(raw)
        item = re.sub(r"\s+", " ", item).strip(" \t\r\n-|–—:/")
        if not item:
            continue
        marker = re.sub(r"[\s\u200c\-_–—|/\\:.,،؛;]+", "", item).casefold()
        if marker and marker not in seen:
            seen.add(marker)
            result.append(item[:120])
    return ",".join(result)[:500]


def terms_for_site(site):
    if not site:
        return []
    values = [site.name, site.hostname, site.hostname.split(".")[0]]
    normalized_custom = normalize_terms(site.brand_terms)
    if normalized_custom:
        values.extend(normalized_custom.split(","))
    result = []
    seen = set()
    for value in values:
        item = _normalize_text(value).strip()
        marker = re.sub(r"[\s\u200c\-_–—|/\\:.,،؛;]+", "", item).casefold()
        if item and marker and marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _term_pattern(term):
    normalized = _normalize_text(term).strip()
    if not normalized:
        return ""
    tokens = [
        token for token in re.split(r"[\s\u200c\u200f\ufeff\-_–—|/\\:.,،؛;()\[\]{}]+", normalized)
        if token
    ]
    if not tokens:
        return ""

    encoded = []
    for token in tokens:
        chars = []
        for char in token:
            chars.append(_CHAR_PATTERNS.get(char, re.escape(char)))
        encoded.append("".join(chars))
    return _SEP_PATTERN.join(encoded)


def strip_terms(value, terms):
    """Remove cleanup phrases despite ZWNJ/spacing and Arabic/Persian variants."""
    text = _normalize_text(value)
    if not text:
        return ""
    ordered = sorted((str(x or "") for x in terms if str(x or "").strip()), key=len, reverse=True)
    for term in ordered:
        pattern = _term_pattern(term)
        if pattern:
            text = re.sub(pattern, " ", text, flags=re.I)
    text = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", " ", text)
    text = re.sub(r"\s*[-|–—:/]+\s*(?=$)", " ", text)
    text = re.sub(r"(?<=\s)[-|–—:/]+(?=\s)", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n-|–—:/")
    return text


def install():
    """Upgrade the legacy sanitizer globals used by v22/v27 at runtime."""
    legacy._terms_for_site = terms_for_site
    legacy._strip_terms = strip_terms
    legacy.normalize_brand_terms = normalize_terms


def _clean_offer(offer):
    payload = dict(offer.payload or {})
    product = offer.product
    raw = {
        "name": payload.get("name") or product.name,
        "description": payload.get("description") or product.description,
        "specs": payload.get("specs") or product.specs or {},
        "categories": list(offer.category_path or []),
    }
    cleaned = legacy.sanitize_scraped_text(raw, offer.source_url)
    new_payload = dict(payload)
    new_payload["name"] = cleaned.get("name") or ""
    new_payload["description"] = cleaned.get("description") or ""
    new_payload["specs"] = dict(cleaned.get("specs") or {})
    new_path = list(cleaned.get("categories") or [])
    changed = new_payload != payload or new_path != list(offer.category_path or [])
    if changed:
        offer.payload = new_payload
        offer.category_path = new_path
    return changed


@transaction.atomic
def apply_existing_terms(site):
    """Apply current terms to stored offers now, then rebuild visible products.

    This never fetches the source website. It edits only already-stored source
    payloads, so saving a cleanup term has an immediate visible effect. Future
    syncs use the same matcher and therefore keep the cleanup in place.
    """
    install()
    offers = list(
        ProductSourceOffer.objects.filter(source_site=site)
        .select_related("product")
        .order_by("id")
    )
    changed_offers = []
    affected_product_ids = set()
    offer_product_ids = set()
    for offer in offers:
        offer_product_ids.add(offer.product_id)
        if _clean_offer(offer):
            changed_offers.append(offer)
            affected_product_ids.add(offer.product_id)

    if changed_offers:
        ProductSourceOffer.objects.bulk_update(changed_offers, ["payload", "category_path"], batch_size=200)

    rebuilt = 0
    if affected_product_ids:
        from shop.services.source_identity_v22 import aggregate_product
        for product in Product.objects.filter(pk__in=affected_product_ids).order_by("id"):
            aggregate_product(product)
            rebuilt += 1

    # Compatibility for historical synced products that predate ProductSourceOffer.
    legacy_changed = []
    legacy_rows = Product.objects.filter(
        source_type=Product.SYNCED,
        source_url__icontains=site.hostname,
    )
    if offer_product_ids:
        legacy_rows = legacy_rows.exclude(pk__in=offer_product_ids)
    for product in legacy_rows.iterator(chunk_size=200):
        cleaned = legacy.sanitize_scraped_text({
            "name": product.name,
            "description": product.description,
            "specs": product.specs or {},
            "categories": [],
        }, product.source_url)
        before = (product.name, product.description, product.specs)
        product.name = (cleaned.get("name") or product.name)[:300]
        product.description = (cleaned.get("description") or "")[:12000]
        product.specs = dict(cleaned.get("specs") or {})
        if before != (product.name, product.description, product.specs):
            legacy_changed.append(product)
    if legacy_changed:
        Product.objects.bulk_update(legacy_changed, ["name", "description", "specs"], batch_size=200)

    return {
        "offers_scanned": len(offers),
        "offers_changed": len(changed_offers),
        "products_rebuilt": rebuilt,
        "legacy_products_changed": len(legacy_changed),
        "products_changed": rebuilt + len(legacy_changed),
    }


install()
