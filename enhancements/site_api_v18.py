"""Delta bot API v29 cleanup-term endpoint over the v17/v22 API chain."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.models import SourceSite
from shop.services import source_catalog_v22  # noqa: F401,E402
from shop.services.source_terms_v29 import apply_existing_terms, normalize_terms

from .site_api import _authorized, _json, _not_found, _unauthorized
from .site_api_v17 import bot_api as v17_bot_api


@csrf_exempt
def bot_api(request):
    # Keep every existing endpoint untouched. Cleanup terms get one dedicated
    # endpoint because saving a filter should affect already-imported products
    # immediately, not only future scrapes.
    if request.method == "POST":
        data = _json(request)
        action = str(data.get("action") or "")
        if action == "delta_source_terms_update":
            if not _authorized(request):
                return _unauthorized()
            payload = data.get("payload") or {}
            site = SourceSite.objects.filter(pk=payload.get("id")).first()
            if not site:
                return _not_found("source_site")
            try:
                site.brand_terms = normalize_terms(payload.get("brand_terms") or "")
                site.save(update_fields=["brand_terms"])
                stats = apply_existing_terms(site) if payload.get("apply_existing", True) else {
                    "offers_scanned": 0,
                    "offers_changed": 0,
                    "products_rebuilt": 0,
                    "legacy_products_changed": 0,
                    "products_changed": 0,
                }
                return JsonResponse({
                    "ok": True,
                    "data": {
                        "id": site.id,
                        "brand_terms": site.brand_terms,
                        **stats,
                    },
                })
            except Exception as exc:
                return JsonResponse({
                    "ok": False,
                    "error": "source_terms_update_failed",
                    "detail": str(exc)[:700],
                }, status=500)

    return v17_bot_api(request)
