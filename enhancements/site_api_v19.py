from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.models import SiteSetting

from .help_pages import ensure_default_help_pages, help_page_row, make_help_slug
from .models import HelpPage
from .site_api import _authorized, _json, _not_found
from .site_api_v18 import bot_api as v18_bot_api


def _sync_legacy_terms(value):
    ensure_default_help_pages()
    item = HelpPage.objects.filter(slug="rules").first()
    if item is not None:
        item.content = str(value or "").strip()[:50000]
        item.save(update_fields=["content", "updated_at"])


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    # Old Telegram messages can still contain the former direct rules edit button.
    # Keep that path compatible instead of allowing SiteSetting.terms_text and the
    # new HelpPage-backed rules page to diverge.
    if action in {"delta_commerce_update", "settings_update"} and "terms_text" in payload:
        response = v18_bot_api(request)
        if response.status_code < 400:
            try:
                _sync_legacy_terms(payload.get("terms_text"))
            except Exception:
                pass
        return response

    if not action.startswith("delta_help_page"):
        return v18_bot_api(request)

    try:
        ensure_default_help_pages()

        if action == "delta_help_pages":
            rows = HelpPage.objects.order_by("sort_order", "id")[:100]
            return JsonResponse({"ok": True, "data": [help_page_row(x) for x in rows]})

        if action == "delta_help_page_detail":
            item = HelpPage.objects.filter(pk=payload.get("id")).first()
            if not item:
                return _not_found("help_page")
            return JsonResponse({"ok": True, "data": help_page_row(item)})

        if action == "delta_help_page_create":
            title = str(payload.get("title") or "").strip()[:140]
            if not title:
                return JsonResponse({"ok": False, "error": "title_required"}, status=400)
            item = HelpPage.objects.create(
                slug=make_help_slug(title),
                title=title,
                content=str(payload.get("content") or "").strip()[:50000],
                is_visible=bool(payload.get("is_visible", True)),
                is_builtin=False,
                sort_order=max(0, int(payload.get("sort_order") or 100)),
            )
            return JsonResponse({"ok": True, "data": help_page_row(item)})

        if action == "delta_help_page_update":
            item = HelpPage.objects.filter(pk=payload.get("id")).first()
            if not item:
                return _not_found("help_page")
            if "title" in payload:
                title = str(payload.get("title") or "").strip()[:140]
                if not title:
                    return JsonResponse({"ok": False, "error": "title_required"}, status=400)
                item.title = title
            if "content" in payload:
                item.content = str(payload.get("content") or "").strip()[:50000]
            if "is_visible" in payload:
                item.is_visible = bool(payload.get("is_visible"))
            if "sort_order" in payload:
                item.sort_order = max(0, int(payload.get("sort_order") or 0))
            item.save()
            if item.slug == "rules" and "content" in payload:
                store = SiteSetting.load()
                store.terms_text = item.content
                store.save(update_fields=["terms_text"])
            return JsonResponse({"ok": True, "data": help_page_row(item)})

        if action == "delta_help_page_delete":
            item = HelpPage.objects.filter(pk=payload.get("id")).first()
            if not item:
                return _not_found("help_page")
            if item.is_builtin:
                return JsonResponse({"ok": False, "error": "builtin_help_page_protected"}, status=409)
            row = help_page_row(item)
            item.delete()
            return JsonResponse({"ok": True, "data": row})

        return JsonResponse({"ok": False, "error": "unknown_help_page_action"}, status=400)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": "delta_help_page_action_failed", "detail": str(exc)[:700]}, status=500)
