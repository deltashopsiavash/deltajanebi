from django.test import TestCase

from shop.management.commands import telegram_bot_v7
from shop.models import Product, SourceSite
from shop.services.source_catalog import _looks_product_url, apply_site_markup_to_existing


class BulkCatalogSettingsTests(TestCase):
    def setUp(self):
        self.site = SourceSite.objects.create(
            name="فروشگاه تست",
            base_url="https://example.com",
            hostname="example.com",
            bulk_import_enabled=True,
            default_markup_type=SourceSite.MARKUP_PERCENT,
            default_markup_value=20,
        )

    def test_source_defaults_are_stored(self):
        self.assertTrue(self.site.bulk_import_enabled)
        self.assertEqual(self.site.markup_label(), "20%")

    def test_site_markup_updates_existing_source_product(self):
        product = Product.objects.create(
            name="محصول تست",
            price=100000,
            source_price=100000,
            stock=1,
            source_type=Product.SYNCED,
            source_url="https://example.com/product/test/",
        )
        apply_site_markup_to_existing(self.site)
        product.refresh_from_db()
        self.assertEqual(product.price, 120000)
        self.assertEqual(product.markup_type, Product.MARKUP_PERCENT)

    def test_manual_product_price_keeps_priority_over_site_default(self):
        product = Product.objects.create(
            name="محصول دستی",
            price=100000,
            source_price=100000,
            stock=1,
            source_type=Product.SYNCED,
            source_url="https://example.com/product/manual/",
            manual_price_override=177000,
        )
        apply_site_markup_to_existing(self.site)
        product.refresh_from_db()
        self.assertEqual(product.price, 177000)

    def test_bot_exposes_bulk_price_and_catalog_purge_controls(self):
        source_labels = [button.text for row in telegram_bot_v7.source_actions(self.site).inline_keyboard for button in row]
        main_labels = [button.text for row in telegram_bot_v7.main_menu().inline_keyboard for button in row]
        self.assertTrue(any("آپلود همه" in label for label in source_labels))
        self.assertIn("💵 قیمت", source_labels)
        self.assertIn("🧹 پاکسازی محصولات", main_labels)

    def test_common_product_urls_are_detected(self):
        self.assertTrue(_looks_product_url("https://example.com/product/cable-1/"))
        self.assertTrue(_looks_product_url("https://example.com/products/cable-2"))
        self.assertFalse(_looks_product_url("https://example.com/product-category/cable/"))
