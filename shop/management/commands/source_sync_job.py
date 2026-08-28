from django.core.management.base import BaseCommand

from shop.services.source_bulk_job_v21 import run_full_sync


class Command(BaseCommand):
    help = "Run one DeltaJanebi v21 source-catalog sync job in a detached process."

    def add_arguments(self, parser):
        parser.add_argument("job_id")

    def handle(self, *args, **options):
        result = run_full_sync(options["job_id"])
        self.stdout.write(self.style.SUCCESS(f"source sync job {options['job_id']}: {result.get('status')}"))
