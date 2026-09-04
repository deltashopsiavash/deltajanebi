"""Delta bot API v16 compatibility endpoints backed by the durable v27 queue."""
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.models import SourceSite
from shop.services.source_job_store_v26 import create_or_get_active_job, read_job

from .site_api import _authorized, _json
from .site_api_v15 import bot_api as v15_bot_api


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    if action == "delta_source_sync_start":
        target_id = payload.get("source_site_id")
        target = None
        if target_id not in (None, "", 0, "0"):
            try:
                target_id = int(target_id)
            except (TypeError, ValueError):
                return JsonResponse({"ok": False, "error": "invalid_source_site_id"}, status=400)
            target = SourceSite.objects.filter(pk=target_id).first()
            if not target:
                return JsonResponse({"ok": False, "error": "source_site_not_found"}, status=404)
            if not target.is_active:
                return JsonResponse({"ok": False, "error": "source_site_inactive"}, status=409)

        job, reused = create_or_get_active_job(
            target_source_site_id=(target.pk if target else 0),
            target_source_site_name=(target.name if target else ""),
        )
        return JsonResponse({
            "ok": True,
            "data": {
                **job,
                "reused": bool(reused),
            },
        })

    if action == "delta_source_sync_status":
        job_id = str(payload.get("job_id") or "")
        job = read_job(job_id)
        if not job:
            return JsonResponse({"ok": False, "error": "sync_job_not_found"}, status=404)
        return JsonResponse({"ok": True, "data": job})

    return v15_bot_api(request)
