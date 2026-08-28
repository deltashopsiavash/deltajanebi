import uuid
from unittest.mock import patch

from django.test import TestCase

from shop.models import SourceSite
from shop.services.source_bulk_job import _job_path
from shop.services.source_bulk_job_v21 import run_full_sync


class SourceBulkJobV21Tests(TestCase):
    def setUp(self):
        self.first, _ = SourceSite.objects.update_or_create(
            hostname="hamrahedovom.ir",
            defaults={
                "name": "همراه دوم",
                "base_url": "https://hamrahedovom.ir",
                "is_active": True,
                "bulk_import_enabled": True,
            },
        )
        self.second, _ = SourceSite.objects.update_or_create(
            hostname="marivanphone.com",
            defaults={
                "name": "مریوان فون",
                "base_url": "https://marivanphone.com",
                "is_active": True,
                "bulk_import_enabled": True,
            },
        )

    def test_existing_products_are_synced_before_discovery_for_each_source(self):
        events = []

        def existing(site):
            return [f"{site.base_url}/product/known"]

        def discover(site, **kwargs):
            events.append(("discover", site.hostname))
            progress = kwargs.get("progress")
            if progress:
                progress({
                    "requests": 1,
                    "found": 1,
                    "elapsed": 1,
                    "budget": kwargs.get("budget_seconds") or 75,
                    "current_url": f"{site.base_url}/sitemap.xml",
                })
            return [f"{site.base_url}/product/new"], {
                "requests": 1,
                "found": 1,
                "elapsed": 1,
                "budget": kwargs.get("budget_seconds") or 75,
                "timed_out": False,
            }

        def upsert(site, url):
            kind = "new" if url.endswith("/new") else "known"
            events.append((kind, site.hostname))
            return object(), kind == "new", {"new": True} if kind == "new" else {}

        job_id = "test-v21-" + uuid.uuid4().hex
        try:
            with (
                patch("shop.services.source_bulk_job_v21._existing_urls", side_effect=existing),
                patch("shop.services.source_bulk_job_v21.discover_product_urls_bounded", side_effect=discover),
                patch("shop.services.source_bulk_job_v21.upsert_source_product_with_changes", side_effect=upsert),
                patch("shop.services.source_bulk_job_v21.backfill_existing_offers", return_value=0),
                patch("shop.services.source_bulk_job_v21.consolidate_sibling_duplicates", return_value={"categories_merged": 0, "products_recategorized": 0}),
                patch("shop.services.source_bulk_job_v21.consolidate_duplicate_products", return_value={"products_merged": 0, "offers_moved": 0}),
            ):
                result = run_full_sync(job_id)

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["checked"], 4)
            self.assertEqual(result["total"], 4)
            self.assertEqual(result["created"], 2)
            self.assertEqual(
                events,
                [
                    ("known", "hamrahedovom.ir"),
                    ("discover", "hamrahedovom.ir"),
                    ("new", "hamrahedovom.ir"),
                    ("known", "marivanphone.com"),
                    ("discover", "marivanphone.com"),
                    ("new", "marivanphone.com"),
                ],
            )
        finally:
            path = _job_path(job_id)
            if path.exists():
                path.unlink()
