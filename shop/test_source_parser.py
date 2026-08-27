from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import SimpleTestCase, TestCase

from shop.models import Product, SourceSite
from shop.services import source_sync
from shop.services.source_catalog import CatalogSkip, upsert_source_product


class SourcePriceParserTests(SimpleTestCase):
    @staticmethod
    def _meta_factory(soup):
        def meta(*keys):
            for key in keys:
                tag = soup.find("meta", property=key) or soup.find("meta", attrs={"name": key})
                if tag and tag.get("content"):
                    return tag["content"]
            return ""
        return meta

    def test_aggregate_offer_low_price_irr_is_converted_to_toman(self):
        soup = BeautifulSoup("<html></html>", "lxml")
        ld = {
            "@type": "Product",
            "offers": {
                "@type": "AggregateOffer",
                "lowPrice": "2300000",
                "highPrice": "2600000",
                "priceCurrency": "IRR",
            },
        }
        price, source = source_sync._extract_product_price(soup, ld, self._meta_factory(soup))
        self.assertEqual(price, 230000)
        self.assertEqual(source, "jsonld.lowPrice")

    def test_woocommerce_sale_price_is_extracted(self):
        soup = BeautifulSoup(
            """
            <div class="summary">
              <p class="price">
                <del><span class="woocommerce-Price-amount">280,000 تومان</span></del>
                <ins><span class="woocommerce-Price-amount"><bdi>230,000 تومان</bdi></span></ins>
              </p>
            </div>
            """,
            "lxml",
        )
        price, source = source_sync._extract_product_price(soup, {}, self._meta_factory(soup))
        self.assertEqual(price, 230000)
        self.assertTrue(source.startswith("selector:"))

    def test_woocommerce_variation_price_is_extracted(self):
        soup = BeautifulSoup(
            """
            <form class="variations_form" data-product_variations='[
              {"display_price": 245000, "display_regular_price": 270000, "variation_is_visible": true},
              {"display_price": 235000, "display_regular_price": 260000, "variation_is_visible": true}
            ]'></form>
            """,
            "lxml",
        )
        price, source = source_sync._extract_product_price(soup, {}, self._meta_factory(soup))
        self.assertEqual(price, 235000)
        self.assertEqual(source, "woocommerce.variation")


class SourceUnavailablePriceTests(TestCase):
    def setUp(self):
        # Migration 0010 intentionally seeds hamrahedovom.ir on every fresh database.
        # Reuse that production seed in tests instead of violating hostname uniqueness.
        self.site, _ = SourceSite.objects.update_or_create(
            hostname="hamrahedovom.ir",
            defaults={
                "name": "همراه دوم تست",
                "base_url": "https://hamrahedovom.ir",
                "default_markup_type": SourceSite.MARKUP_PERCENT,
                "default_markup_value": 10,
                "is_active": True,
            },
        )

    def test_existing_unavailable_product_keeps_last_known_price(self):
        product = Product.objects.create(
            name="محصول قبلی",
            source_type=Product.SYNCED,
            source_url="https://hamrahedovom.ir/product/example/",
            source_price=200000,
            price=220000,
            stock=5,
        )
        payload = {
            "name": "محصول قبلی",
            "description": "",
            "price": 0,
            "price_missing": True,
            "price_source": "",
            "stock": 0,
            "image_url": "",
            "gallery": [],
            "specs": {},
            "sku": "",
            "categories": [],
            "source_url": product.source_url,
        }
        with patch("shop.services.source_catalog.source_sync.scrape_product", return_value=payload):
            synced, created = upsert_source_product(self.site, product.source_url)
        self.assertFalse(created)
        synced.refresh_from_db()
        self.assertEqual(synced.source_price, 200000)
        self.assertEqual(synced.price, 220000)
        self.assertEqual(synced.stock, 0)

    def test_new_unavailable_product_without_price_is_skipped(self):
        url = "https://hamrahedovom.ir/product/new-no-price/"
        payload = {
            "name": "محصول ناموجود جدید",
            "description": "",
            "price": 0,
            "price_missing": True,
            "price_source": "",
            "stock": 0,
            "image_url": "",
            "gallery": [],
            "specs": {},
            "sku": "",
            "categories": [],
            "source_url": url,
        }
        with patch("shop.services.source_catalog.source_sync.scrape_product", return_value=payload):
            with self.assertRaises(CatalogSkip):
                upsert_source_product(self.site, url)
        self.assertFalse(Product.objects.filter(source_url=url).exists())
