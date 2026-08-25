from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0006_alter_user_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="banner",
            name="mobile_image",
            field=models.ImageField(blank=True, upload_to="site/banners/mobile/%Y/%m/"),
        ),
        migrations.AddField(
            model_name="banner",
            name="mobile_image_url",
            field=models.URLField(blank=True, max_length=4096),
        ),
    ]
