import math
from collections import defaultdict

from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.models import Category, Product

# Install the current catalog policy before importing the legacy API chain. This
# preserves every existing endpoint while keeping single-product calls aligned
# with the current category/identity behavior as full sync.
from shop.services import source_catalog_v22  # noqa: F401
from shop.services.category_v22 import sync_category_path

from .site_api import _authorized, _json
from .site_api_v14 import _product_row
from .site_api_v16 import bot_api as v16_bot_api


PAGE_SIZE = 45


def _category_descendant_ids(category_id):
    """Return category + every descendant without relying on recursive SQL."""
    pairs = list(Category.objects.values_list("id", "parent_id"))
    children = defaultdict(list)
    for item_id, parent_id in pairs:
        children[parent_id].append(item_id)
    result = []
    queue = [int(category_id)]
    seen = set()
    while queue:
        item_id = queue.pop(0)
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item_id)
        queue.extend(children.get(item_id, []))
    return result


def _ordered_categories():
    items = list(
        Category.objects.annotate(
            active_product_count=Count("products", filter=Q(products__is_active=True))
        ).order_by("order", "name", "id")
    )
    by_parent = defaultdict(list)
    by_id = {item.id: item for item in items}
    for item in items:
        by_parent[item.parent_id].append(item)

    count_cache = {}

    def subtree_count(item_id, trail=None):
        if item_id in count_cache:
            return count_cache[item_id]
        trail = set(trail or ())
        if item_id in trail:
            return int(getattr(by_id.get(item_id), "active_product_count", 0) or 0)
        trail.add(item_id)
        item = by_id.get(item_id)
        total = int(getattr(item, "active_product_count", 0) or 0)
        for child in by_parent.get(item_id, []):
            total += subtree_count(child.id, trail)
        count_cache[item_id] = total
        return total

    ordered = []
    visited = set()

    def walk(item, path, depth):
        if item.id in visited:
            return
        visited.add(item.id)
        current_path = [*path, item.name]
        direct = int(item.active_product_count or 0)
        ordered.append({
            "id": item.id,
            "name": item.name,
            "path": " > ".join(current_path),
            "depth": depth,
            "is_active": item.is_active,
            "parent_id": item.parent_id,
            "has_image": bool(item.image_url),
            "direct_product_count": direct,
            "product_count": subtree_count(item.id),
        })
        for child in by_parent.get(item.id, []):
            walk(child, current_path, depth + 1)

    for root in by_parent.get(None, []):
        walk(root, [], 0)
    # Defensive fallback for orphaned/cyclic legacy rows: show them rather than
    # silently hiding them from management.
    for item in items:
        if item.id not in visited:
            parent_path = []
            parent = by_id.get(item.parent_id)
            if parent and parent.id in visited:
                parent_path = [parent.name]
            walk(item, parent_path, 0 if not parent_path else 1)
    return ordered


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    if action == "categories":
        return JsonResponse({"ok": True, "data": _ordered_categories()})

    if action == "category_create":
        name = str(payload.get("name") or "").strip()[:120]
        if not name:
            return JsonResponse({"ok": False, "error": "name_required"}, status=400)
        parent = Category.objects.filter(pk=payload.get("parent_id")).first() if payload.get("parent_id") else None
        path = [item.name for item in parent.ancestor_chain()] if parent else []
        item = sync_category_path([*path, name])
        return JsonResponse({"ok": True, "data": {"id": item.id, "name": item.name, "reused": item.name != ""}})

    if action == "delta_products":
        mode = str(payload.get("mode") or "all").strip().lower()
        query = str(payload.get("query") or "").strip()
        try:
            page = max(1, int(payload.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            category_id = max(0, int(payload.get("category_id") or 0))
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "invalid_category_id"}, status=400)

        category = None
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

        if category_id:
            category = Category.objects.filter(pk=category_id).first()
            if not category:
                return JsonResponse({"ok": False, "error": "category_not_found"}, status=404)
            rows = rows.filter(category_id__in=_category_descendant_ids(category_id))

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
            "category": ({"id": category.id, "name": category.name} if category else None),
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
