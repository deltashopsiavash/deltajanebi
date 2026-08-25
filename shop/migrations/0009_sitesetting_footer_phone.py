from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shop", "0008_footer_content_trust_badges"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="footer_phone",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
