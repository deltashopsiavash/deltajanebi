from django.db import models
from django.utils import timezone


URL_MAX_LENGTH = 4096


class ProductSourceOffer(models.Model):
    product = models.ForeignKey(
        "shop.Product",
        on_delete=models.CASCADE,
        related_name="source_offers_v21",
    )
    source_site = models.ForeignKey(
        "shop.SourceSite",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="product_offers_v21",
    )
    source_url = models.URLField(max_length=URL_MAX_LENGTH, unique=True)
    source_product_code = models.CharField(max_length=120, blank=True)
    model_key = models.CharField(max_length=180, blank=True, db_index=True)
    source_price = models.PositiveBigIntegerField(default=0)
    sale_price = models.PositiveBigIntegerField(default=0)
    stock = models.PositiveIntegerField(default=0)
    category_path = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product_id", "source_site_id", "id"]
        indexes = [
            models.Index(fields=["source_site", "model_key"], name="shop_offer_site_model_idx"),
            models.Index(fields=["product", "is_active"], name="shop_offer_product_active_idx"),
        ]

    def __str__(self):
        return f"{self.product_id} | {self.source_site_id or '-'} | {self.model_key or self.source_url}"
