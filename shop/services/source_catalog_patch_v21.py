"""Compatibility bridge for Delta source sync v21.

Existing API/button flows continue importing ``shop.services.source_catalog``;
only the implementation behind the existing functions is upgraded. No legacy
management action is removed.
"""
from shop.services import source_catalog as legacy
from shop.services import source_catalog_v21 as modern

legacy.upsert_source_product = modern.upsert_source_product
legacy.upsert_source_product_with_changes = modern.upsert_source_product_with_changes
legacy.import_unpriced_catalog_product = modern.import_unpriced_catalog_product
