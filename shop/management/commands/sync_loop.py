import os
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from shop.management.commands.telegram_bot_v8 import _change_lines, _chunk_changes
from shop.models import Product, SourceSite
from shop.services.source_bulk_job_v26 import _existing_urls
from shop.services.source_isolation_v26 import run_discovery_isolated, run_product_isolated
from shop.services.source_job_store_v26 import has_pending_manual_job
from shop.services.source_sync_lock import catalog_sync_lock
from shop.services.telegram_notify import notify_admins

AUTO_DISCOVERY_BUDGET = max(30, min(int(os.getenv("DELTA_AUTO_DISCOVERY_BUDGET", "180")), 600))
AUTO_DISCOVERY_MAX_SITEMAPS = max(30, min(int(os.getenv("DELTA_AUTO_DISCOVERY_MAX_SITEMAPS", "250")), 1000))
AUTO_DISCOVERY_MAX_PAGES = max(40, min(int(os.getenv("DELTA_AUTO_DISCOVERY_MAX_PAGES", "400")), 1500))


def _catalog_urls_bounded(site):
    """Refresh known offers and discover additions in a killable child process."""
    existing = list(dict.fromkeys(_existing_urls(site)))
    if not site.bulk_import_enabled or has_pending_manual_job():
        return existing, False

    discovered, meta, warning = run_discovery_isolated(
        site,
        budget_seconds=AUTO_DISCOVERY_BUDGET,
        max_sitemaps=AUTO_DISCOVERY_MAX_SITEMAPS,
        max_pages=AUTO_DISCOVERY_MAX_PAGES,
        stop_requested=has_pending_manual_job,
    )
    if not meta.get("stopped"):
        site.last_discovered_count = len(discovered)
        site.save(update_fields=["last_discovered_count"])
    return list(dict.fromkeys([*discovered, *existing])), bool(meta.get("stopped"))


class Command(BaseCommand):
    def _run_cycle(self):
        sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))
        if not sites:
            self.stdout.write("No active source sites.")
            return False

        change_lines = []
        errors = []
        checked = 0
        skipped = 0
        product_delay = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.08")), 2.0))

        for site in sites:
            if has_pending_manual_job():
                self.stdout.write("Manual catalog job queued/running; automatic cycle yielded.")
                return True

            self.stdout.write(f"Syncing source: {site.name} ({site.hostname})")
            try:
                urls, yielded = _catalog_urls_bounded(site)
                if yielded or has_pending_manual_job():
                    self.stdout.write("Automatic discovery yielded to a manual catalog job.")
                    return True
            except Exception as exc:
                errors.append(f"{site.name} discovery: {str(exc)[:160]}")
                urls = list(dict.fromkeys(_existing_urls(site)))

            for url in urls:
                if has_pending_manual_job():
                    self.stdout.write("Manual catalog job queued/running; automatic product loop yielded.")
                    return True

                checked += 1
                result = run_product_isolated(site, url)
                skipped += int(result.get("skipped") or 0)
                if result.get("errors") and len(errors) < 20:
                    errors.append(f"{site.name}: {str(result.get('warning') or 'خطای Sync')[:160]}\n🔗 {url}")

                if result.get("created") or result.get("changed"):
                    product_id = int(result.get("product_id") or 0)
                    product = Product.objects.filter(pk=product_id).first() if product_id else None
                    if product:
                        try:
                            change_lines.extend(
                                _change_lines(
                                    site,
                                    product,
                                    bool(result.get("created")),
                                    result.get("changes") or {},
                                )
                            )
                        except Exception as exc:
                            if len(errors) < 20:
                                errors.append(f"{site.name}: گزارش تغییرات: {str(exc)[:160]}")
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
        return False

    def handle(self, *args, **opts):
        interval = max(300, int(os.getenv("SOURCE_SYNC_INTERVAL", "1800")))
        initial_delay = max(0, min(int(os.getenv("DELTA_AUTO_SYNC_INITIAL_DELAY", "300")), interval))
        self.stdout.write(
            f"Automatic catalog sync interval: {interval} seconds; initial delay: {initial_delay} seconds"
        )

        if initial_delay:
            time.sleep(initial_delay)

        while True:
            try:
                if has_pending_manual_job():
                    self.stdout.write("Manual catalog sync pending; automatic cycle skipped.")
                else:
                    with catalog_sync_lock() as acquired:
                        if not acquired:
                            self.stdout.write("Catalog sync busy in another container; automatic cycle skipped.")
                        else:
                            self._run_cycle()
            except Exception as exc:
                self.stderr.write(str(exc))
                notify_admins(f"⚠️ خطای کلی Sync خودکار: {str(exc)[:700]}")
            time.sleep(interval)
