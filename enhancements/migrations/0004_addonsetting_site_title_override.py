from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("enhancements", "0003_helppage"),
    ]

    operations = [
        migrations.AddField(
            model_name="addonsetting",
            name="site_title_override",
            field=models.CharField(
                blank=True,
                help_text="عنوان موقت/اختصاصی تگ title؛ خالی باشد از نام فروشگاه استفاده می‌شود.",
                max_length=240,
            ),
        ),
    ]
