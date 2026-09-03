import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.management.commands.telegram_bot_v8 import _change_lines, _chunk_changes
from shop.models import SourceSite
from shop.services.source_bulk_job_v21 import _existing_urls
from shop.services.source_bulk_job_v22 import ProductDeadlineExceeded, _product_deadline
from shop.services.source_catalog_v22 import CatalogSkip, upsert_source_product_with_changes
from shop.services.source_discovery_v19 import discover_product_urls_bounded
from shop.services.source_sync import SourceNotProductError
from shop.services.source_sync_lock import catalog_sync_lock
from shop.services.telegram_notify import notify_admins

AUTO_DISCOVERY_BUDGET = max(30, min(int(os.getenv("DELTA_AUTO_DISCOVERY_BUDGET", "180")), 600))
AUTO_DISCOVERY_MAX_SITEMAPS = max(30, min(int(os.getenv("DELTA_AUTO_DISCOVERY_MAX_SITEMAPS", "250")), 1000))
AUTO_DISCOVERY_MAX_PAGES = max(40, min(int(os.getenv("DELTA_AUTO_DISCOVERY_MAX_PAGES", "400")), 1500))


def _catalog_urls_bounded(site):
    """Refresh known offers and discover additions without an unbounded crawl."""
    existing = list(dict.fromkeys(_existing_urls(site)))
    if not site.bulk_import_enabled:
        return existing

    discovered, meta = discover_product_urls_bounded(
        site,
        budget_seconds=AUTO_DISCOVERY_BUDGET,
        max_sitemaps=AUTO_DISCOVERY_MAX_SITEMAPS,
        max_pages=AUTO_DISCOVERY_MAX_PAGES,
    )
    site.last_discovered_count = len(discovered)
    site.save(update_fields=["last_discovered_count"])
    return list(dict.fromkeys([*discovered, *existing]))


class Command(BaseCommand):
    def _run_cycle(self):
        sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))
        if not sites:
            self.stdout.write("No active source sites.")
            return

        change_lines = []
        errors = []
        checked = 0
        skipped = 0
        product_delay = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.08")), 2.0))

        for site in sites:
            self.stdout.write(f"Syncing source: {site.name} ({site.hostname})")
            try:
                urls = _catalog_urls_bounded(site)
            except Exception as exc:
                errors.append(f"{site.name} discovery: {str(exc)[:160]}")
                urls = list(dict.fromkeys(_existing_urls(site)))

            for url in urls:
                checked += 1
                try:
                    with _product_deadline():
                        product, created, changes = upsert_source_product_with_changes(site, url)
                    if created or changes:
                        change_lines.extend(_change_lines(site, product, created, changes))
                except ProductDeadlineExceeded as exc:
                    if len(errors) < 20:
                        errors.append(f"{site.name}: {str(exc)[:160]}\n🔗 {url}")
                except (SourceNotProductError, CatalogSkip):
                    skipped += 1
                except Exception as exc:
                    if len(errors) < 20:
                        errors.append(f"{site.name}: {str(exc)[:160]}\n🔗 {url}")
                finally:
                    if product_delay:
                        time.sleep(product_delay)

            if site.bulk_import_enabled:
                site.last_bulk_sync_at = timezone.now()
                site.save(update_fields=["last_bulk_sync_at"])

        self.stdout.write(
            f"Cycle complete. checked={checked}, skipped={skipped}, "
            f"change_blocks={len(change_lines)}, errors={len(errors)}"
        )
        now = timezone.localtime().strftime("%Y/%m/%d - %H:%M")

        if change_lines:
            chunks = _chunk_changes(change_lines, limit=3300)
            for index, chunk in enumerate(chunks, 1):
                notify_admins(
                    f"🔄 گزارش همگام‌سازی خودکار\n"
                    f"🕒 {now} به وقت ایران\n"
                    f"📦 بررسی‌شده: {checked:,}\n"
                    f"📋 بخش {index}/{len(chunks)}\n\n"
                    f"{chunk}"
                )
        else:
            notify_admins(
                f"✅ همگام‌سازی خودکار انجام شد\n"
                f"🕒 {now} به وقت ایران\n"
                f"📦 بررسی‌شده: {checked:,}\n"
                "تغییری در محصولات پیدا نشد."
            )

        if errors:
            notify_admins(
                "⚠️ خطاهای همگام‌سازی خودکار\n\n"
                + "\n\n".join(f"• {item}" for item in errors[:12])
            )

    def handle(self, *args, **opts):
        interval = max(300, int(os.getenv("SOURCE_SYNC_INTERVAL", "1800")))
        initial_delay = max(0, min(int(os.getenv("DELTA_AUTO_SYNC_INITIAL_DELAY", "300")), interval))
        self.stdout.write(
            f"Automatic catalog sync interval: {interval} seconds; initial delay: {initial_delay} seconds"
        )

        # update-site recreates this container. Previously it immediately started
        # a second full crawl exactly when the administrator typically pressed
        # «همگام‌سازی همه», so two independent containers processed the same
        # catalog concurrently. Give manual maintenance a quiet startup window.
        if initial_delay:
            time.sleep(initial_delay)

        while True:
            try:
                with catalog_sync_lock() as acquired:
                    if not acquired:
                        self.stdout.write("Catalog sync busy in another container; automatic cycle skipped.")
                    else:
                        self._run_cycle()
            except Exception as exc:
                self.stderr.write(str(exc))
                notify_admins(f"⚠️ خطای کلی Sync خودکار: {str(exc)[:700]}")
            time.sleep(interval)
