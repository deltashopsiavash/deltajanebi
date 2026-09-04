"""Durable catalog-sync job queue/state for Delta v27.

The old implementation kept progress in /tmp JSON files owned by the web
container. Manual jobs are stored in PostgreSQL so web, supervisor and Telegram
always observe one durable state. A job may target all active source sites or a
single SourceSite while still using the same deployment-wide catalog lock.
"""
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from enhancements.models import SourceCatalogJob


TERMINAL = {
    SourceCatalogJob.COMPLETED,
    SourceCatalogJob.FAILED,
    SourceCatalogJob.CANCELLED,
}
ACTIVE = {SourceCatalogJob.QUEUED, SourceCatalogJob.RUNNING}
MAX_WARNINGS = 20


def initial_state(target_source_site_id=0, target_source_site_name=""):
    try:
        target_id = max(0, int(target_source_site_id or 0))
    except (TypeError, ValueError):
        target_id = 0
    return {
        "phase": "queued",
        "sites": 0,
        "site_index": 0,
        "total": 0,
        "checked": 0,
        "created": 0,
        "changed": 0,
        "skipped": 0,
        "errors": 0,
        "categories_merged": 0,
        "products_recategorized": 0,
        "products_merged": 0,
        "products_deleted": 0,
        "products_split": 0,
        "offers_moved": 0,
        "offers_backfilled": 0,
        "identity_refreshed": 0,
        "current_site": "",
        "current_url": "",
        "phase_total": 0,
        "phase_checked": 0,
        "discover_scanned": 0,
        "discover_found": 0,
        "discover_elapsed": 0,
        "discover_budget": 0,
        "item_started_at": 0,
        "item_timeout": 0,
        "item_mode": "",
        "warnings": [],
        "target_source_site_id": target_id,
        "target_source_site_name": str(target_source_site_name or "")[:200],
        "sync_scope": "single_source" if target_id else "all_sources",
        "engine_version": 27,
        "job_store": "database",
    }


def _seconds_since(value):
    if not value:
        return None
    return max(0, int((timezone.now() - value).total_seconds()))


def job_to_dict(job):
    if not job:
        return None
    data = initial_state()
    data.update(dict(job.state or {}))
    data.update({
        "job_id": job.job_id,
        "status": job.status,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "heartbeat_age": _seconds_since(job.heartbeat_at),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "engine_version": 27,
        "job_store": "database",
    })
    return data


def active_job():
    return SourceCatalogJob.objects.filter(status__in=ACTIVE).order_by("created_at").first()


def create_or_get_active_job(target_source_site_id=0, target_source_site_name=""):
    """Create one durable job, optionally scoped to exactly one source site.

    Only one manual catalog job may be active deployment-wide. If another job is
    already queued/running, callers reattach to it instead of creating a second
    writer; its original scope is intentionally preserved.
    """
    current = active_job()
    if current:
        return job_to_dict(current), True

    state = initial_state(target_source_site_id, target_source_site_name)
    job_id = uuid.uuid4().hex
    try:
        job = SourceCatalogJob.objects.create(
            job_id=job_id,
            status=SourceCatalogJob.QUEUED,
            state=state,
            active_slot=1,
        )
        return job_to_dict(job), False
    except IntegrityError:
        # Another gunicorn worker won the race against the conditional unique
        # constraint. Reattach the caller to that job instead of starting two.
        current = active_job()
        if current:
            return job_to_dict(current), True
        raise


def read_job(job_id):
    job = SourceCatalogJob.objects.filter(job_id=str(job_id or "")).first()
    return job_to_dict(job)


def _status_from(payload, fallback):
    value = str((payload or {}).get("status") or fallback or "").strip().lower()
    valid = {choice[0] for choice in SourceCatalogJob.STATUS_CHOICES}
    return value if value in valid else fallback


def write_job(job_id, payload):
    """Merge a state heartbeat instead of replacing the whole job document."""
    job_id = str(job_id or "").strip()
    if not job_id:
        raise ValueError("invalid_job_id")
    payload = dict(payload or {})
    now = timezone.now()

    with transaction.atomic():
        job = SourceCatalogJob.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            job = SourceCatalogJob.objects.create(
                job_id=job_id,
                status=SourceCatalogJob.QUEUED,
                state=initial_state(),
                active_slot=1,
            )

        previous_status = job.status
        status = _status_from(payload, previous_status)
        merged = initial_state()
        merged.update(dict(job.state or {}))
        for key, value in payload.items():
            if key not in {"job_id", "status", "created_at", "updated_at", "heartbeat_at", "heartbeat_age"}:
                merged[key] = value
        merged["engine_version"] = 27
        merged["job_store"] = "database"
        target_id = int(merged.get("target_source_site_id") or 0)
        merged["sync_scope"] = "single_source" if target_id else "all_sources"

        job.state = merged
        job.status = status
        fields = ["state", "status", "updated_at"]

        if status == SourceCatalogJob.RUNNING:
            job.heartbeat_at = now
            fields.append("heartbeat_at")
            if not job.started_at:
                job.started_at = now
                fields.append("started_at")
        elif status == SourceCatalogJob.QUEUED:
            # Queue status itself is durable; supervisor heartbeat starts only
            # after the job is claimed.
            pass
        elif status in TERMINAL:
            job.heartbeat_at = now
            job.finished_at = job.finished_at or now
            fields.extend(["heartbeat_at", "finished_at"])

        # auto_now fields are included explicitly when update_fields is used.
        job.updated_at = now
        job.save(update_fields=list(dict.fromkeys(fields)))
    return job_to_dict(job)


def claim_next_job():
    now = timezone.now()
    with transaction.atomic():
        job = (
            SourceCatalogJob.objects.select_for_update(skip_locked=True)
            .filter(status=SourceCatalogJob.QUEUED)
            .order_by("created_at")
            .first()
        )
        if not job:
            return None
        job.status = SourceCatalogJob.RUNNING
        job.started_at = job.started_at or now
        job.heartbeat_at = now
        state = initial_state()
        state.update(dict(job.state or {}))
        state.update({"phase": "waiting_worker", "engine_version": 27, "job_store": "database"})
        job.state = state
        job.save(update_fields=["status", "started_at", "heartbeat_at", "state", "updated_at"])
        return job_to_dict(job)


def has_pending_manual_job():
    return SourceCatalogJob.objects.filter(status__in=ACTIVE).exists()


def mark_failed(job_id, error, message, **extra):
    data = {
        "status": SourceCatalogJob.FAILED,
        "phase": "failed",
        "error": str(error or "source_sync_failed")[:120],
        "message": str(message or "همگام‌سازی ناموفق بود.")[:1200],
    }
    data.update(extra)
    return write_job(job_id, data)


def recover_orphaned_running_jobs(message="سرویس Sync دوباره راه‌اندازی شد و اجرای قبلی متوقف شده بود."):
    """Fail stale RUNNING rows when the dedicated supervisor boots."""
    recovered = 0
    ids = list(SourceCatalogJob.objects.filter(status=SourceCatalogJob.RUNNING).values_list("job_id", flat=True))
    for job_id in ids:
        mark_failed(job_id, "sync_supervisor_restarted", message)
        recovered += 1
    return recovered


def prune_old_jobs(days=14):
    cutoff = timezone.now() - timedelta(days=max(1, int(days)))
    return SourceCatalogJob.objects.filter(status__in=TERMINAL, finished_at__lt=cutoff).delete()[0]
