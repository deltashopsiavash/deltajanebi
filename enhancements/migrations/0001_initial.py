# Generated for additive SanaShop-parity features.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("shop", "0015_order_wallet_payments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AddonSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("backup_interval_minutes", models.PositiveIntegerField(default=0)),
                ("last_backup_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="BotEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(db_index=True, max_length=60)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("acknowledged_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="ProductStory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("media", models.FileField(upload_to="stories/%Y/%m/")),
                ("media_type", models.CharField(choices=[("image", "عکس"), ("video", "ویدئو")], default="image", max_length=10)),
                ("target_url", models.CharField(max_length=500)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["sort_order", "-id"]},
        ),
        migrations.CreateModel(
            name="EmailVerificationCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=6)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("last_sent_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="email_otp", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="ProductAmazing",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price", models.PositiveBigIntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=False)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("product", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="amazing_offer", to="shop.product")),
            ],
            options={"verbose_name": "قیمت شگفت‌انگیز", "verbose_name_plural": "قیمت‌های شگفت‌انگیز"},
        ),
    ]
