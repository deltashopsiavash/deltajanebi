"""Delta bot API v30: source cleanup plus independent homepage category cloning."""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.models import SourceSite
from shop.services import source_catalog_v22  # noqa: F401,E402
from shop.services.home_category_clone_v30 import (
    clone_homepage_categories,
    homepage_category_status,
    reset_homepage_categories,
)
from shop.services.source_terms_v29 import apply_existing_terms, normalize_terms

from .site_api import _authorized, _json, _not_found, _unauthorized
from .site_api_v17 import bot_api as v17_bot_api


@csrf_exempt
def bot_api(request):
    # Keep every existing endpoint untouched. v29/v30 actions are intercepted
    # here and everything else falls through to the stable v17 API chain.
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

        if action in {"delta_home_categories_status", "delta_home_categories_clone", "delta_home_categories_reset"}:
            if not _authorized(request):
                return _unauthorized()
            payload = data.get("payload") or {}
            try:
                if action == "delta_home_categories_status":
                    result = homepage_category_status()
                elif action == "delta_home_categories_reset":
                    result = reset_homepage_categories()
                else:
                    result = clone_homepage_categories(payload.get("source_site_id"))
                return JsonResponse({"ok": True, "data": result})
            except (TypeError, ValueError) as exc:
                return JsonResponse({
                    "ok": False,
                    "error": "home_categories_clone_failed",
                    "detail": str(exc)[:700],
                }, status=400)
            except Exception as exc:
                return JsonResponse({
                    "ok": False,
                    "error": "home_categories_clone_failed",
                    "detail": str(exc)[:700],
                }, status=500)

    return v17_bot_api(request)
