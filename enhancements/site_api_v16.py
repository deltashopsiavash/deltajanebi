import os
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


JOB_LOG_DIR = Path("/tmp/deltajanebi-source-sync-jobs")


def _worker_alive(pid):
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            parts = stat.read_text(encoding="utf-8", errors="ignore").split()
            if len(parts) >= 3 and parts[2] == "Z":
                return False
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _dead_worker_job(job_id, job):
    current = dict(job or {})
    if current.get("status") not in {"queued", "running"}:
        return current
    pid = current.get("worker_pid")
    if not pid or _worker_alive(pid):
        return current

    log_tail = ""
    try:
        raw = (JOB_LOG_DIR / f"{job_id}.log").read_bytes()
        log_tail = raw[-2400:].decode("utf-8", errors="replace").strip()
    except OSError:
        pass

    current.update({
        "status": "failed",
        "phase": "failed",
        "error": "sync_worker_exited",
        "message": "پردازش همگام‌سازی متوقف شده است؛ وضعیت قبلی دیگر به‌صورت Running نمایش داده نمی‌شود.",
    })
    if log_tail:
        current["worker_log_tail"] = log_tail
    return write_job(job_id, current)


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
        JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_handle = (JOB_LOG_DIR / f"{job_id}.log").open("ab")
        try:
            process = subprocess.Popen(
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

        # Preserve state in case the child already advanced from queued -> running
        # before Popen returned, and only add its OS pid for liveness checks.
        job = read_job(job_id) or {"status": "queued"}
        job["worker_pid"] = process.pid
        write_job(job_id, job)
        return JsonResponse({"ok": True, "data": {"job_id": job_id, "status": job.get("status", "queued")}})

    if action == "delta_source_sync_status":
        job_id = str(payload.get("job_id") or "")
        job = read_job(job_id)
        if not job:
            return JsonResponse({"ok": False, "error": "sync_job_not_found"}, status=404)
        job = _dead_worker_job(job_id, job)
        return JsonResponse({"ok": True, "data": job})

    return v15_bot_api(request)
