import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from shop.models import Category, Product, SourceSite
from shop.services.category_normalizer import category_key, consolidate_duplicate_categories, sync_category_path
from shop.services.source_catalog_v20 import scrape_product_with_retry, upsert_source_product_with_changes
from shop.services.source_sync import SourceSyncError


class CanonicalCategoryV20Tests(TestCase):
    def test_leaf_only_and_rich_path_reuse_one_category(self):
        loose = Category.objects.create(name="کابل ها", slug="")
        product = Product.objects.create(name="کابل تست", price=1000, stock=1, category=loose)

        leaf = sync_category_path(["لوازم جانبی موبایل", "کابل‌ها"])
        loose.refresh_from_db()
        product.refresh_from_db()

        self.assertEqual(leaf.id, loose.id)
        self.assertIsNotNone(leaf.parent_id)
        self.assertEqual(leaf.parent.name, "لوازم جانبی موبایل")
        self.assertEqual(product.category_id, leaf.id)
        self.assertEqual(
            sum(1 for item in Category.objects.all() if category_key(item.name) == category_key("کابل‌ها")),
            1,
        )

    def test_existing_duplicate_root_is_merged_into_richer_hierarchy(self):
        parent = Category.objects.create(name="لوازم خانگی", slug="")
        nested = Category.objects.create(name="باتری", slug="", parent=parent)
        loose = Category.objects.create(name="باتری", slug="")
        product = Product.objects.create(name="باتری تست", price=1000, stock=1, category=loose)

        stats = consolidate_duplicate_categories()
        product.refresh_from_db()

        self.assertGreaterEqual(stats["categories_merged"], 1)
        self.assertEqual(product.category_id, nested.id)
        self.assertFalse(Category.objects.filter(pk=loose.id).exists())
        self.assertEqual(
            sum(1 for item in Category.objects.all() if category_key(item.name) == category_key("باتری")),
            1,
        )

    def test_existing_synced_product_gets_category_corrected(self):
        site = SourceSite.objects.create(
            name="Source",
            base_url="https://example.com",
            hostname="example.com",
            is_active=True,
        )
        product = Product.objects.create(
            name="AUX قدیمی",
            price=100000,
            source_price=100000,
            stock=1,
            source_type=Product.SYNCED,
            source_url="https://example.com/product/aux",
        )
        data = {
            "name": "کابل صدا AUX",
            "description": "",
            "price": 120000,
            "stock": 3,
            "image_url": "",
            "gallery": [],
            "specs": {},
            "sku": "",
            "categories": ["لوازم جانبی موبایل", "کابل ها"],
            "source_url": product.source_url,
        }
        with patch("shop.services.source_catalog_v20.source_sync.scrape_product", return_value=data):
            updated, created, changes = upsert_source_product_with_changes(site, product.source_url)

        self.assertFalse(created)
        self.assertIn("category_id", changes)
        self.assertEqual(updated.category.name, "کابل ها")
        self.assertEqual(updated.category.parent.name, "لوازم جانبی موبایل")

    def test_network_failure_is_retried_before_counting_as_error(self):
        data = {"name": "ok"}
        with patch(
            "shop.services.source_catalog_v20.source_sync.scrape_product",
            side_effect=[SourceSyncError("ارتباط با سایت منبع برقرار نشد: timeout"), data],
        ) as mocked, patch("shop.services.source_catalog_v20.time.sleep"):
            result = scrape_product_with_retry("https://example.com/product/x", attempts=3)
        self.assertEqual(result, data)
        self.assertEqual(mocked.call_count, 2)


class ProductPaginationV20Tests(TestCase):
    API_KEY = "test-delta-v20-api-key-0123456789abcdef"

    def setUp(self):
        self.env = patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.API_KEY, "DOMAIN": "delta.example"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client()
        for index in range(61):
            Product.objects.create(name=f"محصول {index:03d}", price=1000 + index, stock=1)

    def api(self, page):
        return self.client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": "delta_products", "payload": {"mode": "all", "page": page}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )

    def test_all_products_are_paginated_25_per_page(self):
        first = self.api(1)
        second = self.api(2)
        third = self.api(3)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(len(first.json()["data"]), 25)
        self.assertEqual(len(second.json()["data"]), 25)
        self.assertEqual(len(third.json()["data"]), 11)
        self.assertEqual(first.json()["pagination"]["total"], 61)
        self.assertEqual(first.json()["pagination"]["pages"], 3)
        self.assertEqual(first.json()["pagination"]["per_page"], 25)
        self.assertTrue(first.json()["pagination"]["has_next"])
        self.assertFalse(third.json()["pagination"]["has_next"])
