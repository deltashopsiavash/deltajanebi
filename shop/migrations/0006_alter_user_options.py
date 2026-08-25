from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0005_product_management_banner_social"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="user",
            options={"verbose_name": "user", "verbose_name_plural": "users"},
        ),
    ]
