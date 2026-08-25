from django.db.models import Count, Prefetch, Q

from .models import Category, SiteSetting, SocialLink, TrustBadge


def _nav_queryset():
    return (
        Category.objects.filter(is_active=True)
        .annotate(active_child_count=Count("children", filter=Q(children__is_active=True)))
        .order_by("-active_child_count", "order", "name")
    )


def store_context(request):
    try:
        settings = SiteSetting.load()
    except Exception:
        settings = None

    cart = request.session.get("cart", {}) if hasattr(request, "session") else {}

    nav_categories = []
    if settings:
        active_categories = _nav_queryset()
        nav_categories = list(
            _nav_queryset()
            .filter(parent__isnull=True)
            .prefetch_related(
                Prefetch("children", queryset=active_categories),
                Prefetch("children__children", queryset=active_categories),
            )[:12]
        )

    enamad_badge = TrustBadge.objects.filter(is_active=True, badge_type=TrustBadge.ENAMAD).first() if settings else None
    zarinpal_badge = TrustBadge.objects.filter(is_active=True, badge_type=TrustBadge.ZARINPAL).first() if settings else None

    return {
        "store_settings": settings,
        "nav_categories": nav_categories,
        "social_links": SocialLink.objects.filter(is_active=True)[:12] if settings else [],
        "enamad_badge": enamad_badge,
        "zarinpal_badge": zarinpal_badge,
        "cart_count": sum(int(v) for v in cart.values()),
    }
