import time

from django.core.management.base import BaseCommand

from shop.services.order_workflow import expire_reservations
from shop.services.telegram_notify import notify_admins


class Command(BaseCommand):
    help = "Expire unpaid stock reservations every minute."

    def handle(self, *args, **options):
        self.stdout.write("reservation worker started")
        while True:
            try:
                expired = expire_reservations(limit=500)
                if expired:
                    notify_admins(
                        "⏱ رزرو ۴۵ دقیقه‌ای این سفارش‌ها منقضی و لغو شد:\n"
                        + "، ".join(f"#{oid}" for oid in expired)
                    )
                    self.stdout.write(f"expired {len(expired)} reservations")
            except Exception as exc:
                self.stderr.write(f"reservation worker error: {exc}")
            time.sleep(60)
