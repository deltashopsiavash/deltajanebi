from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("enhancements", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceCatalogJob",
            fields=[
                ("job_id", models.CharField(max_length=32, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "در صف"),
                            ("running", "در حال اجرا"),
                            ("completed", "تکمیل‌شده"),
                            ("failed", "ناموفق"),
                            ("cancelled", "لغوشده"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("state", models.JSONField(blank=True, default=dict)),
                ("active_slot", models.PositiveSmallIntegerField(default=1, editable=False)),
                ("heartbeat_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="sourcecatalogjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["queued", "running"])),
                fields=("active_slot",),
                name="enh_one_active_source_catalog_job",
            ),
        ),
    ]
