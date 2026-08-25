from django.db.models import Count, Prefetch, Q

from .models import Category, Product, SiteSetting, SocialLink, TrustBadge


def _nav_queryset():
    return (
        Category.objects.filter(is_active=True)
        .annotate(active_child_count=Count("children", filter=Q(children__is_active=True), distinct=True))
        .order_by("-active_child_count", "order", "name")
    )


def store_context(request):
    try:
        settings = SiteSetting.load()
    except Exception:
        settings = None

    raw_cart = request.session.get("cart", {}) if hasattr(request, "session") else {}
    clean_cart = {}
    if isinstance(raw_cart, dict) and raw_cart:
        ids = [int(key) for key in raw_cart.keys() if str(key).isdigit()]
        products = {p.id: p for p in Product.objects.filter(id__in=ids, is_active=True, stock__gt=0).only("id", "stock")}
        for pid, product in products.items():
            try:
                qty = max(0, min(int(raw_cart.get(str(pid), 0)), product.stock))
            except (TypeError, ValueError):
                qty = 0
            if qty:
                clean_cart[str(pid)] = qty
        if clean_cart != raw_cart and hasattr(request, "session"):
            request.session["cart"] = clean_cart
            request.session.modified = True

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
        "cart_count": sum(clean_cart.values()),
    }
