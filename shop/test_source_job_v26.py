import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase

from enhancements.models import SourceCatalogJob
from shop.models import SourceSite
from shop.services import source_bulk_job_v26 as bulk
from shop.services import source_discovery_v19 as discovery
from shop.services import source_isolation_v26 as isolation
from shop.services import source_job_store_v26 as jobs
from shop import source_registry


class FakeResponse:
    def __init__(self, url, text):
        self.url = url
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = 200
        self.headers = {"Content-Type": "application/xml"}

    def __bool__(self):
        return True


class SourceJobV26Tests(TestCase):
    def setUp(self):
        self.site = SourceSite.objects.create(
            name="Source",
            base_url="https://source.example",
            hostname="source.example",
            is_active=True,
            bulk_import_enabled=True,
        )

    def test_job_state_updates_are_merged_and_heartbeat_is_durable(self):
        job, reused = jobs.create_or_get_active_job()
        self.assertFalse(reused)
        job_id = job["job_id"]

        jobs.write_job(job_id, {
            "status": "running",
            "phase": "syncing_known",
            "checked": 7,
            "current_url": "https://source.example/product/a/",
            "custom_marker": "keep-me",
        })
        second = jobs.write_job(job_id, {
            "status": "running",
            "checked": 8,
        })

        self.assertEqual(second["phase"], "syncing_known")
        self.assertEqual(second["current_url"], "https://source.example/product/a/")
        self.assertEqual(second["custom_marker"], "keep-me")
        self.assertEqual(second["checked"], 8)
        self.assertEqual(second["job_store"], "database")
        self.assertIsNotNone(second["heartbeat_at"])
        self.assertEqual(SourceCatalogJob.objects.get(pk=job_id).status, "running")

    def test_single_source_scope_is_durable_and_selects_only_that_site(self):
        other = SourceSite.objects.create(
            name="Other Source",
            base_url="https://other.example",
            hostname="other.example",
            is_active=True,
            bulk_import_enabled=True,
        )
        job, reused = jobs.create_or_get_active_job(self.site.pk, self.site.name)
        self.assertFalse(reused)
        self.assertEqual(job["target_source_site_id"], self.site.pk)
        self.assertEqual(job["target_source_site_name"], self.site.name)
        self.assertEqual(job["sync_scope"], "single_source")
        self.assertEqual(job["engine_version"], 27)

        persisted = jobs.read_job(job["job_id"])
        sites, target_id = bulk._selected_sites(persisted)
        self.assertEqual(target_id, self.site.pk)
        self.assertEqual([x.pk for x in sites], [self.site.pk])
        self.assertNotIn(other.pk, [x.pk for x in sites])

    def test_active_job_reuse_never_changes_original_scope(self):
        first, reused = jobs.create_or_get_active_job(self.site.pk, self.site.name)
        self.assertFalse(reused)
        second, reused = jobs.create_or_get_active_job()
        self.assertTrue(reused)
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(second["target_source_site_id"], self.site.pk)
        self.assertEqual(second["sync_scope"], "single_source")

    def test_discovery_streams_heartbeats_and_partial_product_urls(self):
        sitemap = """<?xml version='1.0'?><urlset>
        <url><loc>https://source.example/product/one/</loc></url>
        </urlset>"""
        seen_heartbeats = []
        found = []

        def fake_get(url, site, deadline, accept=None, heartbeat=None):
            seen_heartbeats.append(callable(heartbeat))
            if url == "https://source.example/sitemap.xml":
                return FakeResponse(url, sitemap)
            return None

        with patch("shop.services.source_discovery_v19._safe_get", side_effect=fake_get):
            urls, meta = discovery.discover_product_urls_bounded(
                self.site,
                progress=lambda info: None,
                budget_seconds=20,
                max_sitemaps=10,
                max_pages=20,
                on_product=found.append,
            )

        self.assertIn("https://source.example/product/one/", urls)
        self.assertEqual(found, ["https://source.example/product/one/"])
        self.assertTrue(seen_heartbeats)
        self.assertTrue(all(seen_heartbeats))
        self.assertGreaterEqual(meta["found"], 1)

    def test_dns_validation_has_a_real_deadline(self):
        source_registry._DNS_CACHE.clear()

        def slow_resolver(*args, **kwargs):
            time.sleep(0.08)
            return []

        with patch.object(source_registry, "_DNS_TIMEOUT", 0.01), patch(
            "shop.source_registry.socket.getaddrinfo", side_effect=slow_resolver
        ):
            started = time.monotonic()
            with self.assertRaises(ValueError):
                source_registry._validate_public_host("source.example", 443)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.07)

    def test_product_parent_timeout_returns_error_instead_of_hanging(self):
        fake_process = MagicMock()
        fake_process.pid = 424242
        fake_process.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=1), None]
        fake_process.poll.return_value = None

        with tempfile.TemporaryDirectory() as tmp, patch.object(isolation, "TMP_DIR", Path(tmp)), patch.object(
            isolation, "PRODUCT_WALL_SECONDS", 0.01
        ), patch("shop.services.source_isolation_v26.subprocess.Popen", return_value=fake_process), patch(
            "shop.services.source_isolation_v26.kill_process_group"
        ) as killer:
            result = isolation.run_product_isolated(self.site, "https://source.example/product/stuck/")

        killer.assert_called_once_with(fake_process)
        self.assertEqual(result["errors"], 1)
        self.assertIn("product_hard_timeout", result["warning"])
