from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "shop"

    def ready(self):
        from shop.services import source_sync
        from shop.source_registry import allowed_url, source_brand_terms

        source_sync._allowed_url = allowed_url
        source_sync._source_brand_terms = source_brand_terms
