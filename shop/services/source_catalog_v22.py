"""Compatibility bridge and catalog implementation switch for Delta v22."""
from shop.services import source_catalog as legacy
from shop.services import source_catalog_v21 as base
from shop.services import source_sync
from shop.services import category_v22
from shop.services import source_identity_v22 as identity

# v21 is a well-tested upsert engine. Replace the policy functions it resolves
# at runtime rather than duplicating the database/update machinery.
base.canonical_path = category_v22.canonical_path
base.sync_category_path = category_v22.sync_category_path
base.enhanced_category_names = category_v22.enhanced_category_names
base.identity_key = identity.identity_key
base.find_offer = identity.find_offer
base.find_canonical_product = identity.find_canonical_product
base.aggregate_product = identity.aggregate_product
source_sync._extract_category_names = category_v22.enhanced_category_names


def _payload_v22(data):
    return {
        "name": str(data.get("name") or "")[:300],
        "description": str(data.get("description") or "")[:12000],
        "image_url": str(data.get("image_url") or "")[:4096],
        "gallery": list(data.get("gallery") or [])[:12],
        "specs": dict(data.get("specs") or {}),
        "image_rejected": bool(data.get("image_rejected")),
    }


base._payload = _payload_v22

CatalogSkip = base.CatalogSkip
source_products = base.source_products
discover_product_urls = base.discover_product_urls
apply_site_markup_to_existing = base.apply_site_markup_to_existing
scrape_product_with_retry = base.scrape_product_with_retry
upsert_source_product = base.upsert_source_product
upsert_source_product_with_changes = base.upsert_source_product_with_changes
import_unpriced_catalog_product = base.import_unpriced_catalog_product

# Existing API/bot modules still import ``shop.services.source_catalog``. Keep
# those call sites intact while transparently upgrading their implementation.
legacy.upsert_source_product = upsert_source_product
legacy.upsert_source_product_with_changes = upsert_source_product_with_changes
legacy.import_unpriced_catalog_product = import_unpriced_catalog_product
