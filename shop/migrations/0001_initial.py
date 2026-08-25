# Generated for DeltaJanebi initial schema.

import django.contrib.auth.models
import django.contrib.auth.validators
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import shop.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("password", models.CharField(max_length=128, verbose_name="password")),
                ("last_login", models.DateTimeField(blank=True, null=True, verbose_name="last login")),
                ("is_superuser", models.BooleanField(default=False, help_text="Designates that this user has all permissions without explicitly assigning them.", verbose_name="superuser status")),
                ("first_name", models.CharField(blank=True, max_length=150, verbose_name="first name")),
                ("last_name", models.CharField(blank=True, max_length=150, verbose_name="last name")),
                ("is_staff", models.BooleanField(default=False, help_text="Designates whether the user can log into this admin site.", verbose_name="staff status")),
                ("is_active", models.BooleanField(default=True, help_text="Designates whether this user should be treated as active. Unselect this instead of deleting accounts.", verbose_name="active")),
                ("date_joined", models.DateTimeField(default=django.utils.timezone.now, verbose_name="date joined")),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("phone", models.CharField(blank=True, max_length=20)),
                ("groups", models.ManyToManyField(blank=True, help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.", related_name="user_set", related_query_name="user", to="auth.group", verbose_name="groups")),
                ("user_permissions", models.ManyToManyField(blank=True, help_text="Specific permissions for this user.", related_name="user_set", related_query_name="user", to="auth.permission", verbose_name="user permissions")),
            ],
            options={"abstract": False},
            managers=[("objects", shop.models.UserManager())],
        ),
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(allow_unicode=True, max_length=140, unique=True)),
                ("image_url", models.URLField(blank=True)),
                ("order", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["order", "name"]},
        ),
        migrations.CreateModel(
            name="SiteSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("store_name", models.CharField(default="دلتا جانبی", max_length=120)),
                ("logo_url", models.URLField(blank=True)),
                ("home_banner_url", models.URLField(blank=True)),
                ("shipping_cost", models.PositiveBigIntegerField(default=0)),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("card_number", models.CharField(blank=True, max_length=32)),
                ("card_owner", models.CharField(blank=True, max_length=120)),
                ("support_text", models.CharField(blank=True, max_length=240)),
            ],
        ),
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=300)),
                ("slug", models.SlugField(allow_unicode=True, blank=True, max_length=340, unique=True)),
                ("sku", models.CharField(blank=True, max_length=80, null=True, unique=True)),
                ("description", models.TextField(blank=True)),
                ("price", models.PositiveBigIntegerField(default=0, help_text="تومان")),
                ("source_price", models.PositiveBigIntegerField(default=0, help_text="تومان")),
                ("stock", models.PositiveIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("image_url", models.URLField(blank=True)),
                ("gallery", models.JSONField(blank=True, default=list)),
                ("specs", models.JSONField(blank=True, default=dict)),
                ("source_type", models.CharField(choices=[("manual", "عادی"), ("synced", "خاص/همگام")], default="manual", max_length=10)),
                ("source_url", models.URLField(blank=True)),
                ("source_product_code", models.CharField(blank=True, max_length=100)),
                ("markup_type", models.CharField(choices=[("percent", "درصد"), ("fixed", "مبلغ ثابت")], default="percent", max_length=10)),
                ("markup_value", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("sync_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="products", to="shop.category")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="Order",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("receipt_pending", "در انتظار تایید رسید"), ("preparing", "در حال آماده‌سازی"), ("shipped", "ارسال شده"), ("delivered", "تحویل شده"), ("cancelled", "لغو شده")], default="receipt_pending", max_length=30)),
                ("full_name", models.CharField(max_length=160)),
                ("phone", models.CharField(max_length=30)),
                ("province", models.CharField(max_length=80)),
                ("city", models.CharField(max_length=80)),
                ("address", models.TextField()),
                ("postal_code", models.CharField(blank=True, max_length=20)),
                ("subtotal", models.PositiveBigIntegerField(default=0)),
                ("shipping_cost", models.PositiveBigIntegerField(default=0)),
                ("total", models.PositiveBigIntegerField(default=0)),
                ("receipt", models.ImageField(blank=True, upload_to="receipts/%Y/%m/")),
                ("tracking_code", models.CharField(blank=True, max_length=100)),
                ("admin_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="orders", to="shop.user")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("price", models.PositiveBigIntegerField()),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="shop.order")),
                ("product", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="shop.product")),
            ],
        ),
    ]
