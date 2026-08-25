from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shop", "0007_banner_mobile_image")]

    operations = [
        migrations.AddField(model_name="sitesetting", name="address", field=models.TextField(blank=True)),
        migrations.AddField(model_name="sitesetting", name="contact_email", field=models.EmailField(blank=True, max_length=254)),
        migrations.AddField(model_name="sitesetting", name="footer_description", field=models.TextField(blank=True)),
        migrations.AlterField(
            model_name="sociallink",
            name="platform",
            field=models.CharField(
                choices=[
                    ("instagram", "اینستاگرام"),
                    ("telegram", "تلگرام"),
                    ("whatsapp", "واتساپ"),
                    ("youtube", "یوتیوب"),
                    ("aparat", "آپارات"),
                    ("x", "ایکس"),
                    ("facebook", "فیسبوک"),
                    ("other", "سایر"),
                ],
                default="other",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="TrustBadge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("badge_type", models.CharField(choices=[("enamad", "اینماد"), ("zarinpal", "زرین‌پال")], max_length=20, unique=True)),
                ("image", models.ImageField(blank=True, upload_to="site/trust/%Y/%m/")),
                ("image_url", models.URLField(blank=True, max_length=4096)),
                ("target_url", models.URLField(blank=True, max_length=4096)),
                ("is_active", models.BooleanField(default=True)),
            ],
        ),
    ]
