import json
import os
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase

from enhancements.models import SourceCatalogJob
from shop.models import SourceSite
from shop.services import source_bulk_job
from shop.services.source_catalog import discover_product_urls


class FakeResponse:
    def __init__(self, url, text):
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200

    def __bool__(self):
        return True


class SourceCatalogV18Tests(TestCase):
    API_KEY = "test-delta-v18-api-key-0123456789abcdef"

    def setUp(self):
        self.site = SourceSite.objects.create(
            name="Source",
            base_url="https://source.example",
            hostname="source.example",
            bulk_import_enabled=True,
        )

    def test_partial_sitemap_is_merged_with_category_crawl(self):
        product_map = """<?xml version='1.0'?><urlset>
        <url><loc>https://source.example/product/one/</loc></url>
        </urlset>"""
        category_map = """<?xml version='1.0'?><urlset>
        <url><loc>https://source.example/product-category/cables/</loc></url>
        </urlset>"""
        category_page = """<html><body>
        <a href='/product/two/'>Two</a>
        </body></html>"""
        home = "<html><body></body></html>"

        def fake_get(url, site, accept=None):
            if url == "https://source.example/product-sitemap.xml":
                return FakeResponse(url, product_map)
            if url == "https://source.example/category-sitemap.xml":
                return FakeResponse(url, category_map)
            if url == "https://source.example/product-category/cables/":
                return FakeResponse(url, category_page)
            return FakeResponse(url, home)

        with patch("shop.services.source_catalog._robots_sitemaps", return_value=[
            "https://source.example/product-sitemap.xml",
            "https://source.example/category-sitemap.xml",
        ]), patch("shop.services.source_catalog._safe_get", side_effect=fake_get):
            urls = discover_product_urls(self.site)

        self.assertIn("https://source.example/product/one/", urls)
        self.assertIn("https://source.example/product/two/", urls)
        self.assertEqual(len(set(urls)), len(urls))

    def test_upload_all_keeps_valid_unpriced_product_safely(self):
        data = {
            "name": "کابل بدون قیمت",
            "description": "",
            "source_url": "https://source.example/product/unpriced/",
            "sku": "UNPRICED-1",
            "price": 0,
            "stock": 8,
            "image_url": "https://source.example/media/unpriced.jpg",
            "gallery": [],
            "specs": {},
            "categories": ["کابل", "Type-C"],
        }
        with patch("shop.services.source_bulk_job.source_sync.scrape_product", return_value=data):
            product, created = source_bulk_job._import_unpriced_catalog_product(self.site, data["source_url"])

        self.assertTrue(created)
        self.assertEqual(product.source_url, data["source_url"])
        self.assertEqual(product.price, 0)
        self.assertEqual(product.stock, 0)
        self.assertEqual(product.category.name, "Type-C")
        self.assertIn("قیمت منبع", product.sync_error)

    def test_background_sync_api_is_durable_and_duplicate_start_reattaches(self):
        env = patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.API_KEY})
        env.start()
        self.addCleanup(env.stop)
        client = Client()
        SourceCatalogJob.objects.all().delete()

        response = client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": "delta_source_sync_start", "payload": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], "queued")
        self.assertTrue(data["job_id"])
        self.assertFalse(data["reused"])
        self.assertEqual(data["job_store"], "database")

        duplicate = client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": "delta_source_sync_start", "payload": {}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.content)
        second = duplicate.json()["data"]
        self.assertEqual(second["job_id"], data["job_id"])
        self.assertTrue(second["reused"])
        self.assertEqual(SourceCatalogJob.objects.filter(status="queued").count(), 1)

        status = client.post(
            "/api/bot/v1/",
            data=json.dumps({"action": "delta_source_sync_status", "payload": {"job_id": data["job_id"]}}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
        )
        self.assertEqual(status.status_code, 200, status.content)
        self.assertEqual(status.json()["data"]["status"], "queued")
        self.assertEqual(status.json()["data"]["job_store"], "database")

    def test_home_categories_are_compact_circles(self):
        template = Path("templates/shop/home.html").read_text(encoding="utf-8")
        self.assertIn(".home-cat-circle{width:96px;height:96px", template)
        self.assertIn("border-radius:50%!important", template)
        self.assertIn("object-fit:contain!important", template)
        self.assertIn("grid-template-columns:repeat(3,minmax(0,1fr))", template)
        self.assertLess(template.index(".home-cat-section{margin-top:30px}"), template.index("{% if banners %}"))
