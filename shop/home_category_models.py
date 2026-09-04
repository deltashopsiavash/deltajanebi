from urllib.parse import urlencode

from django.db import models
from django.urls import reverse

from shop.models import URL_MAX_LENGTH


class HomeCategoryShowcase(models.Model):
    """Independent homepage category showcase; it never changes menu categories."""

    source_site = models.ForeignKey(
        "shop.SourceSite",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homepage_category_showcases",
    )
    title = models.CharField(max_length=160, default="دسته‌بندی محصولات")
    subtitle = models.CharField(max_length=240, blank=True)
    source_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    enabled = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "چیدمان دسته‌بندی صفحه اصلی"
        verbose_name_plural = "چیدمان دسته‌بندی صفحه اصلی"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class HomeCategoryTile(models.Model):
    showcase = models.ForeignKey(
        HomeCategoryShowcase,
        on_delete=models.CASCADE,
        related_name="tiles",
    )
    source_site = models.ForeignKey(
        "shop.SourceSite",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homepage_category_tiles",
    )
    category = models.ForeignKey(
        "shop.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homepage_tiles",
    )
    name = models.CharField(max_length=160)
    image_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    source_category_url = models.URLField(max_length=URL_MAX_LENGTH, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["showcase", "is_active", "order"], name="shop_homecat_show_idx"),
        ]

    @property
    def display_image(self):
        if self.image_url:
            return self.image_url
        if self.category_id and self.category and self.category.image_url:
            return self.category.image_url
        return ""

    @property
    def storefront_url(self):
        if self.category_id and self.category and self.category.is_active:
            return reverse("category", args=[self.category.slug])
        return reverse("search") + "?" + urlencode({"q": self.name})

    def __str__(self):
        return f"{self.order}: {self.name}"
