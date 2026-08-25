from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0010_sourcesite_social_platforms")]

    operations = [
        migrations.AddField(
            model_name="sourcesite",
            name="bulk_import_enabled",
            field=models.BooleanField(default=False, help_text="در همگام‌سازی همه، کل کاتالوگ قابل کشف این سایت وارد شود"),
        ),
        migrations.AddField(
            model_name="sourcesite",
            name="default_markup_type",
            field=models.CharField(choices=[("percent", "درصد"), ("fixed", "مبلغ ثابت")], default="percent", max_length=10),
        ),
        migrations.AddField(
            model_name="sourcesite",
            name="default_markup_value",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="sourcesite",
            name="last_bulk_sync_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcesite",
            name="last_discovered_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
