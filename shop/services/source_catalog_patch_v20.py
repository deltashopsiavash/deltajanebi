"""Compatibility bridge for API modules that still import source_catalog.

This keeps old endpoint modules untouched while making their single-product
sync actions use the same resilient/category-canonical v20 implementation.
"""
from shop.services import source_catalog as legacy
from shop.services import source_catalog_v20 as modern

legacy.upsert_source_product = modern.upsert_source_product
legacy.upsert_source_product_with_changes = modern.upsert_source_product_with_changes
