import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand

from shop.models import SourceSite
from shop.services.source_discovery_v19 import discover_product_urls_bounded


class Command(BaseCommand):
    help = "Discover one source catalog in an isolated child and persist heartbeats/partial URLs."

    def add_arguments(self, parser):
        parser.add_argument("site_id", type=int)
        parser.add_argument("result_file")
        parser.add_argument("progress_file")
        parser.add_argument("urls_file")
        parser.add_argument("budget", type=int)
        parser.add_argument("max_sitemaps", type=int)
        parser.add_argument("max_pages", type=int)

    @staticmethod
    def _atomic_json(path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def handle(self, *args, **options):
        result_path = Path(options["result_file"])
        progress_path = Path(options["progress_file"])
        urls_path = Path(options["urls_file"])
        for path in (result_path, progress_path, urls_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        urls_path.touch(exist_ok=True)

        site = SourceSite.objects.get(pk=options["site_id"])
        seen = set()

        def on_product(url):
            value = str(url or "").strip()
            if not value or value in seen:
                return
            seen.add(value)
            with urls_path.open("a", encoding="utf-8") as handle:
                handle.write(value + "\n")
                handle.flush()

        def progress(info):
            payload = dict(info or {})
            payload["site_id"] = site.pk
            payload["site_name"] = site.name
            self._atomic_json(progress_path, payload)

        try:
            urls, meta = discover_product_urls_bounded(
                site,
                progress=progress,
                budget_seconds=options["budget"],
                max_sitemaps=options["max_sitemaps"],
                max_pages=options["max_pages"],
                on_product=on_product,
            )
            # Ensure URLs returned in-memory are also on disk even if a custom
            # discovery implementation skipped the callback.
            for url in urls:
                on_product(url)
            result = {"ok": True, "meta": meta, "found": len(seen)}
        except Exception as exc:
            result = {
                "ok": False,
                "error": "discovery_worker_failed",
                "message": str(exc)[:1200],
                "found": len(seen),
            }
        self._atomic_json(result_path, result)
