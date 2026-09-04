from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.models import SiteSetting

from .models import AddonSetting
from .site_api import _authorized, _json
from .site_api_v19 import bot_api as v19_bot_api


def _title_data():
    addon = AddonSetting.load()
    store = SiteSetting.load()
    override = (addon.site_title_override or "").strip()
    return {
        "title": override,
        "effective_title": override or store.store_name,
        "is_override": bool(override),
    }


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    if action not in {"delta_site_title_get", "delta_site_title_set"}:
        return v19_bot_api(request)

    try:
        if action == "delta_site_title_get":
            return JsonResponse({"ok": True, "data": _title_data()})

        value = str(payload.get("title") or "").strip()
        if value == "-":
            value = ""
        if len(value) > 240:
            return JsonResponse({"ok": False, "error": "site_title_too_long"}, status=400)
        addon = AddonSetting.load()
        addon.site_title_override = value
        addon.save(update_fields=["site_title_override", "updated_at"])
        return JsonResponse({"ok": True, "data": _title_data()})
    except Exception as exc:
        return JsonResponse({"ok": False, "error": "site_title_action_failed", "detail": str(exc)[:700]}, status=500)
