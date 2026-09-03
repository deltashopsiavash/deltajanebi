import json
from pathlib import Path

from django.core.management.base import BaseCommand

from shop.models import SourceSite
from shop.services.source_product_worker_v25 import sync_one


class Command(BaseCommand):
    help = "Synchronize exactly one source product and write a JSON result file."

    def add_arguments(self, parser):
        parser.add_argument("site_id", type=int)
        parser.add_argument("url")
        parser.add_argument("result_file")

    def handle(self, *args, **options):
        result_path = Path(options["result_file"])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            site = SourceSite.objects.get(pk=options["site_id"])
            result = sync_one(site, options["url"])
        except Exception as exc:
            result = {
                "created": 0,
                "changed": 0,
                "skipped": 0,
                "errors": 1,
                "product_id": 0,
                "changes": {},
                "warning": f"single-product-worker: {exc}"[:700],
            }
        tmp = result_path.with_suffix(result_path.suffix + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        tmp.replace(result_path)
