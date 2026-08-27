import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

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

    def test_background_sync_api_returns_immediately_and_status_is_pollable(self):
        env = patch.dict(os.environ, {"DELTAJANEBI_BOT_API_KEY": self.API_KEY})
        env.start()
        self.addCleanup(env.stop)
        client = Client()

        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_lock = source_bulk_job.JOB_DIR, source_bulk_job.LOCK_FILE
            source_bulk_job.JOB_DIR = Path(tmp)
            source_bulk_job.LOCK_FILE = Path(tmp) / "active.lock"
            self.addCleanup(setattr, source_bulk_job, "JOB_DIR", old_dir)
            self.addCleanup(setattr, source_bulk_job, "LOCK_FILE", old_lock)

            process = MagicMock()
            with patch("enhancements.site_api_v16.subprocess.Popen", return_value=process) as popen:
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
            popen.assert_called_once()

            status = client.post(
                "/api/bot/v1/",
                data=json.dumps({"action": "delta_source_sync_status", "payload": {"job_id": data["job_id"]}}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.API_KEY}",
            )
            self.assertEqual(status.status_code, 200, status.content)
            self.assertEqual(status.json()["data"]["status"], "queued")

    def test_home_categories_are_compact_circles(self):
        template = Path("templates/shop/home.html").read_text(encoding="utf-8")
        self.assertIn(".home-cat-circle{width:76px;height:76px", template)
        self.assertIn("border-radius:999px!important", template)
        self.assertIn(".home-cat-circle{width:62px;height:62px}", template)
