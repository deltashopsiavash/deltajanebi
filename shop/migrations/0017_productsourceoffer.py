from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("shop", "0016_merge_duplicate_categories")]

    operations = [
        migrations.CreateModel(
            name="ProductSourceOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_url", models.URLField(max_length=4096, unique=True)),
                ("source_product_code", models.CharField(blank=True, max_length=120)),
                ("model_key", models.CharField(blank=True, db_index=True, max_length=180)),
                ("source_price", models.PositiveBigIntegerField(default=0)),
                ("sale_price", models.PositiveBigIntegerField(default=0)),
                ("stock", models.PositiveIntegerField(default=0)),
                ("category_path", models.JSONField(blank=True, default=list)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("is_active", models.BooleanField(default=True)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="source_offers_v21", to="shop.product")),
                ("source_site", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="product_offers_v21", to="shop.sourcesite")),
            ],
            options={"ordering": ["product_id", "source_site_id", "id"]},
        ),
        migrations.AddIndex(
            model_name="productsourceoffer",
            index=models.Index(fields=["source_site", "model_key"], name="shop_offer_site_model_idx"),
        ),
        migrations.AddIndex(
            model_name="productsourceoffer",
            index=models.Index(fields=["product", "is_active"], name="shop_offer_product_active_idx"),
        ),
    ]
