from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0003_category_parent_product_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="logo",
            field=models.ImageField(blank=True, upload_to="site/logo/"),
        ),
    ]
