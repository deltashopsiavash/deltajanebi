import json
import os
from unittest.mock import patch

from django.test import Client, TestCase

from shop.models import Category, Product, SourceSite


class DeltaSiteApiV27Tests(TestCase):
    API_KEY = "test-delta-v27-api-key-0123456789abcdef"

    def setUp(self):
        self.env = patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.API_KEY, "DOMAIN": "delta.example"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Client()

    def api(self, action, payload=None):
        return self.client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": action, "payload": payload or {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )

    def test_delta_products_returns_every_page_and_filters_category_subtree(self):
        root = Category.objects.create(name="لوازم جانبی", slug="")
        child = Category.objects.create(name="کابل", slug="", parent=root)
        other = Category.objects.create(name="هدفون", slug="")

        # Product.save() generates unique slugs/public codes. Use the production
        # save path here rather than bulk_create, which intentionally bypasses it.
        for index in range(50):
            Product.objects.create(
                name=f"محصول {index}",
                price=1000 + index,
                stock=1,
                category=(child if index >= 46 else root),
            )
        for index in range(3):
            Product.objects.create(name=f"هدفون {index}", price=2000 + index, stock=1, category=other)

        first = self.api("delta_products", {"mode": "all", "page": 1})
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(len(first.json()["data"]), 45)
        self.assertEqual(first.json()["pagination"]["total"], 53)
        self.assertEqual(first.json()["pagination"]["pages"], 2)
        self.assertTrue(first.json()["pagination"]["has_next"])

        second = self.api("delta_products", {"mode": "all", "page": 2})
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(len(second.json()["data"]), 8)
        self.assertFalse(second.json()["pagination"]["has_next"])

        filtered_first = self.api("delta_products", {"mode": "all", "page": 1, "category_id": root.id})
        self.assertEqual(filtered_first.status_code, 200, filtered_first.content)
        data = filtered_first.json()
        self.assertEqual(data["pagination"]["total"], 50)
        self.assertEqual(data["pagination"]["pages"], 2)
        self.assertEqual(data["category"]["id"], root.id)
        self.assertEqual(len(data["data"]), 45)

        filtered_second = self.api("delta_products", {"mode": "all", "page": 2, "category_id": root.id})
        self.assertEqual(len(filtered_second.json()["data"]), 5)

        categories = self.api("categories")
        by_id = {row["id"]: row for row in categories.json()["data"]}
        self.assertEqual(by_id[root.id]["direct_product_count"], 46)
        self.assertEqual(by_id[root.id]["product_count"], 50)
        self.assertEqual(by_id[child.id]["product_count"], 4)

    def test_sync_start_can_target_exactly_one_source_site(self):
        first = SourceSite.objects.create(
            name="Source One",
            base_url="https://source-one.example",
            hostname="source-one.example",
            is_active=True,
            bulk_import_enabled=True,
        )
        SourceSite.objects.create(
            name="Source Two",
            base_url="https://source-two.example",
            hostname="source-two.example",
            is_active=True,
            bulk_import_enabled=True,
        )

        response = self.api("delta_source_sync_start", {"source_site_id": first.id})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertFalse(data["reused"])
        self.assertEqual(data["target_source_site_id"], first.id)
        self.assertEqual(data["target_source_site_name"], "Source One")
        self.assertEqual(data["sync_scope"], "single_source")
        self.assertEqual(data["engine_version"], 27)

        status = self.api("delta_source_sync_status", {"job_id": data["job_id"]})
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.json()["data"]["target_source_site_id"], first.id)

    def test_sync_start_rejects_inactive_source(self):
        inactive = SourceSite.objects.create(
            name="Inactive",
            base_url="https://inactive.example",
            hostname="inactive.example",
            is_active=False,
        )
        response = self.api("delta_source_sync_start", {"source_site_id": inactive.id})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "source_site_inactive")
