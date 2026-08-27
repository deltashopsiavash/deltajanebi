from django.apps import AppConfig


class EnhancementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "enhancements"
    verbose_name = "افزونه‌های مدیریتی دلتا جانبی"

    def ready(self):
        # Keep Delta's original Product model and all old features intact. We only
        # extend the effective-price semantics so every existing cart/order path
        # automatically understands the new amazing price.
        from shop.models import Product
        from .pricing import amazing_price_for, effective_price, is_amazing_active, promotion_label

        Product.base_price = property(lambda product: int(product.price or 0))
        Product.discount_price = property(lambda product: product.sale_price)
        Product.amazing_price_value = property(amazing_price_for)
        Product.is_amazing_active = property(is_amazing_active)
        Product.promotion_label = property(promotion_label)
        Product.effective_price = property(effective_price)

        from . import signals  # noqa: F401
