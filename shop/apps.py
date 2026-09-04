from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shop"

    def ready(self):
        # Register the additive source-offer model without changing the original
        # Product model or any of Delta's existing management flows.
        from shop import source_offer_models  # noqa: F401
        from shop.services import source_sync
        from shop.services.source_sanitizer_v27 import sanitize_scraped_product
        from shop.source_registry import (
            allowed_url,
            generic_category_names,
            source_brand_terms,
            source_context,
            stable_sync_product,
        )
        from shop import signals  # noqa: F401

        source_sync._allowed_url = allowed_url
        source_sync._source_brand_terms = source_brand_terms
        source_sync._extract_category_names = generic_category_names

        if not getattr(source_sync.scrape_product, "_delta_sanitized", False):
            original_scrape = source_sync.scrape_product

            def sanitized_scrape(url):
                with source_context(url):
                    data = original_scrape(url)
                return sanitize_scraped_product(data, data.get("source_url") or url)

            sanitized_scrape._delta_sanitized = True
            sanitized_scrape._delta_original = original_scrape
            source_sync.scrape_product = sanitized_scrape

        source_sync.sync_product = stable_sync_product
