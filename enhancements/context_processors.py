from django.db.models import F, Q
from django.utils import timezone

from .help_pages import ensure_default_help_pages
from .models import HelpPage, ProductAmazing, ProductStory


def enhancement_context(request):
    now = timezone.now()
    try:
        stories = ProductStory.objects.filter(is_active=True, expires_at__gt=now).order_by("sort_order", "-id")[:30]
    except Exception:
        stories = []

    amazing_offers = []
    if getattr(request, "path_info", "") == "/":
        try:
            amazing_offers = list(
                ProductAmazing.objects.filter(
                    is_active=True,
                    price__isnull=False,
                    product__is_active=True,
                    product__stock__gt=F("product__reserved_stock"),
                    price__lt=F("product__price"),
                )
                .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
                .select_related("product", "product__category")
                .order_by("-updated_at")[:16]
            )
        except Exception:
            amazing_offers = []

    try:
        ensure_default_help_pages()
        help_pages = list(HelpPage.objects.filter(is_visible=True).order_by("sort_order", "id")[:30])
    except Exception:
        help_pages = []

    return {
        "product_stories": stories,
        "amazing_offers": amazing_offers,
        "help_pages": help_pages,
    }
