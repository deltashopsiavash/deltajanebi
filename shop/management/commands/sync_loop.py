import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.management.commands.telegram_bot_v8 import _catalog_urls, _change_lines, _chunk_changes
from shop.models import SourceSite
from shop.services.source_catalog import source_products, upsert_source_product_with_changes
from shop.services.telegram_notify import notify_admins


class Command(BaseCommand):
    def _run_cycle(self):
        sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))
        if not sites:
            self.stdout.write("No active source sites.")
            return

        change_lines = []
        errors = []
        checked = 0

        for site in sites:
            self.stdout.write(f"Syncing source: {site.name} ({site.hostname})")
            try:
                urls = _catalog_urls(site)
            except Exception as exc:
                errors.append(f"{site.name} discovery: {str(exc)[:160]}")
                urls = list(source_products(site).exclude(source_url="").values_list("source_url", flat=True))

            for url in urls:
                checked += 1
                try:
                    product, created, changes = upsert_source_product_with_changes(site, url)
                    if created or changes:
                        change_lines.extend(_change_lines(site, product, created, changes))
                except Exception as exc:
                    if len(errors) < 20:
                        errors.append(f"{site.name}: {str(exc)[:160]}")

            if site.bulk_import_enabled:
                site.last_bulk_sync_at = timezone.now()
                site.save(update_fields=["last_bulk_sync_at"])

        self.stdout.write(f"Cycle complete. checked={checked}, change_blocks={len(change_lines)}, errors={len(errors)}")

        # اگر هیچ محصولی تغییر نکرده باشد، گزارش خالی به تلگرام ارسال نمی‌شود.
        if change_lines:
            now = timezone.localtime().strftime("%Y/%m/%d - %H:%M")
            chunks = _chunk_changes(change_lines, limit=3300)
            for index, chunk in enumerate(chunks, 1):
                notify_admins(
                    f"🔄 گزارش همگام‌سازی خودکار\n"
                    f"🕒 {now} به وقت ایران\n"
                    f"📋 بخش {index}/{len(chunks)}\n\n"
                    f"{chunk}"
                )

        if errors:
            notify_admins(
                "⚠️ خطاهای همگام‌سازی خودکار\n\n"
                + "\n".join(f"• {item}" for item in errors[:12])
            )

    def handle(self, *args, **opts):
        interval = max(300, int(os.getenv("SOURCE_SYNC_INTERVAL", "1800")))
        self.stdout.write(f"Automatic catalog sync interval: {interval} seconds")
        while True:
            try:
                self._run_cycle()
            except Exception as exc:
                self.stderr.write(str(exc))
                notify_admins(f"⚠️ خطای کلی Sync خودکار: {str(exc)[:700]}")
            time.sleep(interval)
