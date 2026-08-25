from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0011_sourcesite_bulk_import_defaults")]

    operations = [
        migrations.AddField(model_name="sitesetting", name="packaging_cost", field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name="sitesetting", name="free_shipping_threshold", field=models.PositiveBigIntegerField(default=0, help_text="صفر یعنی غیرفعال")),
        migrations.AddField(model_name="sitesetting", name="hide_out_of_stock", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="sitesetting", name="card_payment_enabled", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="sitesetting", name="zarinpal_payment_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="sitesetting", name="zarinpal_merchant_id", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="sitesetting", name="terms_text", field=models.TextField(blank=True)),
        migrations.CreateModel(
            name="DiscountCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(db_index=True, max_length=60, unique=True)),
                ("discount_type", models.CharField(choices=[("percent", "درصدی"), ("fixed", "مبلغ ثابت")], default="percent", max_length=10)),
                ("value", models.PositiveBigIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
                ("starts_at", models.DateTimeField(blank=True, null=True)),
                ("ends_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddField(model_name="order", name="first_name", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="order", name="last_name", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="order", name="order_note", field=models.TextField(blank=True)),
        migrations.AddField(model_name="order", name="discount_code", field=models.CharField(blank=True, max_length=60)),
        migrations.AddField(model_name="order", name="discount_amount", field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name="order", name="packaging_cost", field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name="order", name="payment_method", field=models.CharField(choices=[("card", "کارت به کارت"), ("zarinpal", "درگاه زرین‌پال")], default="card", max_length=20)),
        migrations.AddField(model_name="order", name="payment_status", field=models.CharField(choices=[("pending", "در انتظار پرداخت"), ("receipt", "رسید ارسال شده"), ("paid", "پرداخت موفق"), ("rejected", "پرداخت رد شده"), ("failed", "پرداخت ناموفق")], default="pending", max_length=20)),
        migrations.AddField(model_name="order", name="receipt_rejection_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="order", name="zarinpal_authority", field=models.CharField(blank=True, db_index=True, max_length=80)),
        migrations.AddField(model_name="order", name="zarinpal_ref_id", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="order", name="zarinpal_card_pan", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="order", name="paid_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(
            model_name="order",
            name="status",
            field=models.CharField(choices=[("payment_pending", "در انتظار پرداخت"), ("receipt_pending", "در انتظار تایید رسید"), ("payment_rejected", "پرداخت رد شده"), ("preparing", "در حال آماده‌سازی"), ("shipped", "ارسال شده"), ("delivered", "تحویل شده"), ("cancelled", "لغو شده")], default="payment_pending", max_length=30),
        ),
    ]
