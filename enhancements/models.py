import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProductAmazing(models.Model):
    product = models.OneToOneField("shop.Product", on_delete=models.CASCADE, related_name="amazing_offer")
    price = models.PositiveBigIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "قیمت شگفت‌انگیز"
        verbose_name_plural = "قیمت‌های شگفت‌انگیز"

    @property
    def active_now(self):
        if not self.is_active or not self.price or self.price >= self.product.price:
            return False
        return not self.expires_at or self.expires_at > timezone.now()


class ProductStory(models.Model):
    MEDIA_IMAGE = "image"
    MEDIA_VIDEO = "video"
    MEDIA_CHOICES = [(MEDIA_IMAGE, "عکس"), (MEDIA_VIDEO, "ویدئو")]

    title = models.CharField(max_length=160)
    media = models.FileField(upload_to="stories/%Y/%m/"))
    media_type = models.CharField(max_length=10, choices=MEDIA_CHOICES, default=MEDIA_IMAGE)
    target_url = models.CharField(max_length=500)
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-id"]

    @property
    def active_now(self):
        return bool(self.is_active and self.expires_at > timezone.now())

    @property
    def remaining_seconds(self):
        if not self.active_now:
            return 0
        return max(0, int((self.expires_at - timezone.now()).total_seconds()))


class EmailVerificationCode(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_otp")
    code = models.CharField(max_length=6, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    last_sent_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def issue(cls, user, lifetime_minutes=10):
        now = timezone.now()
        code = f"{secrets.randbelow(1_000_000):06d}"
        obj, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                "code": code,
                "expires_at": now + timedelta(minutes=lifetime_minutes),
                "last_sent_at": now,
                "attempts": 0,
            },
        )
        return obj

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def matches(self, value):
        return not self.is_expired and self.attempts < 6 and secrets.compare_digest(self.code, str(value or "").strip())


class AddonSetting(models.Model):
    backup_interval_minutes = models.PositiveIntegerField(default=0)
    last_backup_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)


class BotEvent(models.Model):
    kind = models.CharField(max_length=60, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["id"]


class SourceCatalogJob(models.Model):
    """Persistent manual catalog-sync queue/status shared by all containers."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (QUEUED, "در صف"),
        (RUNNING, "در حال اجرا"),
        (COMPLETED, "تکمیل‌شده"),
        (FAILED, "ناموفق"),
        (CANCELLED, "لغوشده"),
    ]

    job_id = models.CharField(max_length=32, primary_key=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=QUEUED, db_index=True)
    state = models.JSONField(default=dict, blank=True)
    active_slot = models.PositiveSmallIntegerField(default=1, editable=False)
    heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["active_slot"],
                condition=models.Q(status__in=["queued", "running"]),
                name="enh_one_active_source_catalog_job",
            )
        ]
