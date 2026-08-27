import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase

from shop.models import SourceSite
from shop.services import source_bulk_job_v19
from shop.services.source_discovery_v19 import discover_product_urls_bounded


class FakeResponse:
    def __init__(self, url, text):
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200

    def __bool__(self):
        return True


class SourceDiscoveryV19Tests(TestCase):
    def setUp(self):
        self.site = SourceSite.objects.create(
            name="Bounded Source",
            base_url="https://bounded.example",
            hostname="bounded.example",
            bulk_import_enabled=True,
        )

    def test_bounded_discovery_emits_live_progress_and_keeps_partial_results(self):
        product_map = """<?xml version='1.0'?><urlset>
        <url><loc>https://bounded.example/product/one/</loc></url>
        </urlset>"""
        home = """<html><body>
        <a href='/product/two/'>Two</a>
        <a href='/product-category/cables/'>Cables</a>
        </body></html>"""
        category = "<html><body><a href='/product/three/'>Three</a></body></html>"
        empty = "<html><body></body></html>"

        def fake_get(url, site, deadline, accept=None):
            if url.endswith("robots.txt"):
                return FakeResponse(url, "Sitemap: https://bounded.example/product-sitemap.xml")
            if url.endswith("product-sitemap.xml"):
                return FakeResponse(url, product_map)
            if url == "https://bounded.example/":
                return FakeResponse(url, home)
            if url == "https://bounded.example/product-category/cables/":
                return FakeResponse(url, category)
            return FakeResponse(url, empty)

        events = []
        with patch("shop.services.source_discovery_v19._safe_get", side_effect=fake_get):
            urls, meta = discover_product_urls_bounded(
                self.site,
                progress=events.append,
                budget_seconds=30,
                max_sitemaps=10,
                max_pages=4,
            )

        self.assertIn("https://bounded.example/product/one/", urls)
        self.assertIn("https://bounded.example/product/two/", urls)
        self.assertIn("https://bounded.example/product/three/", urls)
        self.assertTrue(events)
        self.assertGreaterEqual(max(int(x.get("requests") or 0) for x in events), 1)
        self.assertGreaterEqual(max(int(x.get("found") or 0) for x in events), 1)
        self.assertEqual(meta["found"], len(urls))
        self.assertLessEqual(meta["pages"], 4)

    def test_v19_planner_sets_total_and_finishes_after_discovery(self):
        discovered = [
            "https://bounded.example/product/one/",
            "https://bounded.example/product/two/",
        ]

        def fake_discover(site, progress=None, **kwargs):
            progress({
                "requests": 3,
                "found": 2,
                "elapsed": 2,
                "budget": 120,
                "current_url": "https://bounded.example/product-sitemap.xml",
            })
            return discovered, {
                "timed_out": False,
                "requests": 3,
                "found": 2,
                "elapsed": 2,
                "budget": 120,
            }

        with tempfile.TemporaryDirectory() as tmp:
            old_dir = source_bulk_job_v19.JOB_DIR
            old_lock = source_bulk_job_v19.LOCK_FILE
            source_bulk_job_v19.JOB_DIR = Path(tmp)
            source_bulk_job_v19.LOCK_FILE = Path(tmp) / "active.lock"
            try:
                with patch("shop.services.source_bulk_job_v19.discover_product_urls_bounded", side_effect=fake_discover), patch(
                    "shop.services.source_bulk_job_v19.upsert_source_product_with_changes",
                    return_value=(object(), True, {"new": True}),
                ):
                    result = source_bulk_job_v19.run_full_sync("job-v19-test")
            finally:
                source_bulk_job_v19.JOB_DIR = old_dir
                source_bulk_job_v19.LOCK_FILE = old_lock

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["discover_scanned"], 3)
        self.assertEqual(result["discover_found"], 2)
