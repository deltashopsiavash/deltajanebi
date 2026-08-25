from django.db.models import Prefetch

from .models import Category, SiteSetting


def store_context(request):
    try:
        settings = SiteSetting.load()
    except Exception:
        settings = None

    cart = request.session.get("cart", {}) if hasattr(request, "session") else {}

    nav_categories = []
    if settings:
        active_categories = Category.objects.filter(is_active=True).order_by("order", "name")
        nav_categories = list(
            Category.objects.filter(is_active=True, parent__isnull=True)
            .order_by("order", "name")
            .prefetch_related(
                Prefetch("children", queryset=active_categories),
                Prefetch("children__children", queryset=active_categories),
            )[:12]
        )

    return {
        "store_settings": settings,
        "nav_categories": nav_categories,
        "cart_count": sum(int(v) for v in cart.values()),
    }
