from django.db import migrations, models


def fill_public_codes(apps, schema_editor):
    Product = apps.get_model("shop", "Product")
    for product in Product.objects.filter(public_code__isnull=True).order_by("id"):
        product.public_code = f"DJ-{product.id:06d}"
        product.save(update_fields=["public_code"])


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0004_sitesetting_logo"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="public_code",
            field=models.CharField(blank=True, db_index=True, max_length=24, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="product",
            name="manual_image_url_override",
            field=models.URLField(blank=True, max_length=4096),
        ),
        migrations.AddField(
            model_name="product",
            name="manual_name_override",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="product",
            name="manual_price_override",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="manual_stock_override",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="sale_price",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="sale_starts_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="sale_ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="Banner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(blank=True, max_length=160)),
                ("image", models.ImageField(blank=True, upload_to="site/banners/%Y/%m/")),
                ("image_url", models.URLField(blank=True, max_length=4096)),
                ("target_url", models.URLField(blank=True, max_length=4096)),
                ("is_active", models.BooleanField(default=True)),
                ("order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["order", "-id"]},
        ),
        migrations.CreateModel(
            name="SocialLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("platform", models.CharField(choices=[("instagram", "اینستاگرام"), ("telegram", "تلگرام"), ("whatsapp", "واتساپ"), ("youtube", "یوتیوب"), ("aparat", "آپارات"), ("other", "سایر")], default="other", max_length=20)),
                ("label", models.CharField(max_length=80)),
                ("url", models.URLField(max_length=4096)),
                ("is_active", models.BooleanField(default=True)),
                ("order", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["order", "id"]},
        ),
        migrations.RunPython(fill_public_codes, migrations.RunPython.noop),
    ]
