from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="image_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
        migrations.AlterField(
            model_name="product",
            name="image_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
        migrations.AlterField(
            model_name="product",
            name="source_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="home_banner_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
        migrations.AlterField(
            model_name="sitesetting",
            name="logo_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
    ]
