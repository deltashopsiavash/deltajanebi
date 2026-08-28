import math

from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.models import Product

# Install the v20 catalog behavior before importing the legacy API chain. This
# preserves every existing endpoint while making old single-product sync calls
# use the resilient/category-canonical implementation too.
from shop.services import source_catalog_patch_v20  # noqa: F401

from .site_api import _authorized, _json
from .site_api_v14 import _product_row
from .site_api_v16 import bot_api as v16_bot_api


PAGE_SIZE = 25


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    if action == "delta_products":
        mode = str(payload.get("mode") or "all").strip().lower()
        query = str(payload.get("query") or "").strip()
        try:
            page = max(1, int(payload.get("page") or 1))
        except (TypeError, ValueError):
            page = 1

        rows = Product.objects.select_related("category").order_by("-created_at", "-id")
        if mode == "manual":
            rows = rows.filter(source_type=Product.MANUAL)
        elif mode == "synced":
            rows = rows.filter(source_type=Product.SYNCED)
        elif mode == "offers":
            now = timezone.now()
            rows = rows.filter(sale_price__isnull=False)
            rows = rows.filter(Q(sale_starts_at__isnull=True) | Q(sale_starts_at__lte=now))
            rows = rows.filter(Q(sale_ends_at__isnull=True) | Q(sale_ends_at__gt=now))
        if query:
            rows = rows.filter(
                Q(name__icontains=query)
                | Q(public_code__icontains=query)
                | Q(sku__icontains=query)
                | Q(source_product_code__icontains=query)
            )

        total = rows.count()
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(page, pages)
        start = (page - 1) * PAGE_SIZE
        items = [_product_row(item) for item in rows[start:start + PAGE_SIZE]]
        return JsonResponse({
            "ok": True,
            "data": items,
            "mode": mode,
            "query": query,
            "pagination": {
                "page": page,
                "pages": pages,
                "per_page": PAGE_SIZE,
                "total": total,
                "has_previous": page > 1,
                "has_next": page < pages,
            },
        })

    return v16_bot_api(request)
