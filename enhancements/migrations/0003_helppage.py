from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("enhancements", "0002_sourcecatalogjob"),
    ]

    operations = [
        migrations.CreateModel(
            name="HelpPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=120, unique=True)),
                ("title", models.CharField(max_length=140)),
                ("content", models.TextField(blank=True)),
                ("is_visible", models.BooleanField(db_index=True, default=True)),
                ("is_builtin", models.BooleanField(default=False)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "صفحه راهنما",
                "verbose_name_plural": "صفحات راهنما",
                "ordering": ["sort_order", "id"],
            },
        ),
    ]
