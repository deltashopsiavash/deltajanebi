import json
import os
from unittest.mock import patch

from django.test import RequestFactory, TestCase

from enhancements.site_api_v18 import bot_api
from shop.models import Product, SourceSite
from shop.source_offer_models import ProductSourceOffer
from shop.services.source_terms_v29 import apply_existing_terms, normalize_terms, strip_terms


class SourceTermsV29Tests(TestCase):
    def setUp(self):
        self.site = SourceSite.objects.create(
            name="مریوان فون",
            base_url="https://marivanphone.com",
            hostname="marivanphone.com",
            is_active=True,
            bulk_import_enabled=True,
            default_markup_type=SourceSite.MARKUP_PERCENT,
            default_markup_value=0,
        )

    def _product_with_offer(self):
        product = Product.objects.create(
            name="کابل تست مریوان‌فون ضمانت ویژه",
            description="خرید از مریوان فون با تضمین اصالت و تبلیغ ویژه",
            source_type=Product.SYNCED,
            source_url="https://marivanphone.com/product/test-cable",
            source_price=100000,
            price=100000,
            stock=3,
            specs={"فروشنده": "مریوان فون", "توضیح": "ضمانت ویژه"},
        )
        ProductSourceOffer.objects.create(
            product=product,
            source_site=self.site,
            source_url=product.source_url,
            model_key="name:test-cable-cleanup-v29",
            source_price=100000,
            sale_price=100000,
            stock=3,
            category_path=["لوازم مریوان فون", "کابل"],
            payload={
                "name": product.name,
                "description": product.description,
                "specs": dict(product.specs),
                "image_url": "",
                "gallery": [],
            },
        )
        return product

    def test_normalize_terms_accepts_persian_comma_newlines_and_duplicates(self):
        value = normalize_terms("ضمانت، تبلیغ\nضمانت|  مریوان فون  ")
        self.assertEqual(value, "ضمانت,تبلیغ,مریوان فون")

    def test_strip_terms_matches_half_space_and_persian_arabic_variants(self):
        cleaned = strip_terms(
            "کابل مریوان‌فون | كابل ضمانت ویژه | MARIWAN-PHONE",
            ["مریوان فون", "کابل ضمانت", "MARIWAN PHONE"],
        )
        self.assertNotIn("مریوان", cleaned)
        self.assertNotIn("MARIWAN", cleaned.upper())
        self.assertNotIn("ضمانت", cleaned)

    def test_apply_existing_terms_changes_stored_offer_and_visible_product_immediately(self):
        product = self._product_with_offer()
        self.site.brand_terms = normalize_terms("ضمانت ویژه، تضمین اصالت، تبلیغ ویژه")
        self.site.save(update_fields=["brand_terms"])

        stats = apply_existing_terms(self.site)
        product.refresh_from_db()
        offer = ProductSourceOffer.objects.get(source_site=self.site)

        self.assertGreaterEqual(stats["products_changed"], 1)
        self.assertGreaterEqual(stats["offers_changed"], 1)
        self.assertNotIn("مریوان", product.name)
        self.assertNotIn("ضمانت", product.name)
        self.assertNotIn("مریوان", product.description)
        self.assertNotIn("تضمین اصالت", product.description)
        self.assertNotIn("مریوان", offer.payload["name"])
        self.assertNotIn("ضمانت", offer.payload["name"])
        self.assertTrue(all("مریوان" not in value for value in offer.category_path))

    def test_api_saves_normalized_terms_and_applies_existing_products(self):
        product = self._product_with_offer()
        request = RequestFactory().post(
            "/api/bot/",
            data=json.dumps({
                "action": "delta_source_terms_update",
                "payload": {
                    "id": self.site.id,
                    "brand_terms": "ضمانت ویژه، تبلیغ ویژه",
                    "apply_existing": True,
                },
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-source-terms-key",
        )
        with patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": "test-source-terms-key"}, clear=False):
            response = bot_api(request)

        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content.decode("utf-8"))
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["brand_terms"], "ضمانت ویژه,تبلیغ ویژه")
        self.assertGreaterEqual(body["data"]["products_changed"], 1)
        product.refresh_from_db()
        self.assertNotIn("ضمانت", product.name)
