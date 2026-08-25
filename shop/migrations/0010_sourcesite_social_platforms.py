from django.db import migrations, models


def seed_default_source(apps, schema_editor):
    SourceSite = apps.get_model("shop", "SourceSite")
    SourceSite.objects.get_or_create(
        hostname="hamrahedovom.ir",
        defaults={
            "name": "همراه دوم",
            "base_url": "https://hamrahedovom.ir",
            "brand_terms": "همراه دوم,hamrahedovom",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("shop", "0009_sitesetting_footer_phone")]

    operations = [
        migrations.CreateModel(
            name="SourceSite",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("base_url", models.URLField(max_length=4096)),
                ("hostname", models.CharField(db_index=True, max_length=255, unique=True)),
                ("brand_terms", models.CharField(blank=True, help_text="عبارت‌های برند برای پاک‌سازی متن، جداشده با کاما", max_length=500)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AlterField(
            model_name="sociallink",
            name="platform",
            field=models.CharField(
                choices=[
                    ("instagram", "اینستاگرام"),
                    ("telegram", "تلگرام"),
                    ("whatsapp", "واتساپ"),
                    ("rubika", "روبیکا"),
                    ("eitaa", "ایتا"),
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
        migrations.RunPython(seed_default_source, migrations.RunPython.noop),
    ]
