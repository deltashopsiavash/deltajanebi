from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_customer_codes_and_legacy_orders(apps, schema_editor):
    User = apps.get_model("shop", "User")
    Order = apps.get_model("shop", "Order")
    for user in User.objects.filter(customer_code__isnull=True).iterator():
        user.customer_code = f"CU-{user.pk:07d}"
        user.save(update_fields=["customer_code"])
    User.objects.filter(customer_code="").update(customer_code=None)
    for user in User.objects.filter(customer_code__isnull=True).iterator():
        user.customer_code = f"CU-{user.pk:07d}"
        user.save(update_fields=["customer_code"])

    # Orders created before the reservation system already deducted stock at invoice creation.
    # Mark them as committed/released so the new worker never deducts or restores them again.
    Order.objects.all().update(stock_committed=True, reservation_released=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0012_checkout_payments_discounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="customer_code",
            field=models.CharField(blank=True, db_index=True, max_length=20, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="product",
            name="reserved_stock",
            field=models.PositiveIntegerField(default=0, help_text="تعداد رزرو موقت سفارش‌های پرداخت‌نشده"),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="top_bar_text",
            field=models.CharField(default="خرید آنلاین • قیمت و موجودی به‌روز • ارسال مطمئن", max_length=240),
        ),
        migrations.AddField(
            model_name="order",
            name="reservation_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="stock_committed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="order",
            name="reservation_released",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="Announcement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("text", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="AnnouncementRead",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("read_at", models.DateTimeField(auto_now_add=True)),
                ("announcement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reads", to="shop.announcement")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="announcement_reads", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="announcementread",
            constraint=models.UniqueConstraint(fields=("announcement", "user"), name="unique_announcement_read"),
        ),
        migrations.RunPython(backfill_customer_codes_and_legacy_orders, noop_reverse),
    ]
