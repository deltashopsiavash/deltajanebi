import subprocess
import sys
import uuid
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from shop.services.source_bulk_job import create_queued_job, read_job, write_job

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
        job_id = uuid.uuid4().hex
        create_queued_job(job_id)
        log_dir = Path("/tmp/deltajanebi-source-sync-jobs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_handle = (log_dir / f"{job_id}.log").open("ab")
        try:
            subprocess.Popen(
                [sys.executable, "manage.py", "source_sync_job", job_id],
                cwd=str(settings.BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except Exception as exc:
            log_handle.close()
            write_job(job_id, {"status": "failed", "error": "spawn_failed", "message": str(exc)[:700]})
            return JsonResponse({"ok": False, "error": "spawn_failed", "detail": str(exc)[:700]}, status=500)
        log_handle.close()
        return JsonResponse({"ok": True, "data": {"job_id": job_id, "status": "queued"}})

    if action == "delta_source_sync_status":
        job_id = str(payload.get("job_id") or "")
        job = read_job(job_id)
        if not job:
            return JsonResponse({"ok": False, "error": "sync_job_not_found"}, status=404)
        return JsonResponse({"ok": True, "data": job})

    return v15_bot_api(request)
