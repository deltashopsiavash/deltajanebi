import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from shop.services.source_job_store_v26 import (
    TERMINAL,
    claim_next_job,
    mark_failed,
    prune_old_jobs,
    read_job,
    recover_orphaned_running_jobs,
)


SUPERVISOR_DIR = Path(os.getenv("DELTA_SOURCE_SUPERVISOR_DIR", "/tmp/deltajanebi-source-sync-supervisor"))
POLL_SECONDS = max(0.5, min(float(os.getenv("DELTA_SOURCE_SUPERVISOR_POLL", "1")), 5.0))
DEFAULT_STALE_SECONDS = max(60, min(int(os.getenv("DELTA_SOURCE_JOB_STALE_TIMEOUT", "120")), 900))
CLEANUP_STALE_SECONDS = max(DEFAULT_STALE_SECONDS, min(int(os.getenv("DELTA_SOURCE_CLEANUP_STALE_TIMEOUT", "300")), 1800))
DISCOVERY_STALE_SECONDS = max(DEFAULT_STALE_SECONDS, min(int(os.getenv("DELTA_SOURCE_DISCOVERY_SUPERVISOR_STALE", "120")), 600))


def _kill_group(process):
    if not process:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5)
    except Exception:
        pass


def _tail(path, limit=3000):
    try:
        return Path(path).read_bytes()[-limit:].decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _stale_limit(job):
    phase = str(job.get("phase") or "")
    if phase in {"syncing_known", "syncing_new"}:
        timeout = max(1, int(job.get("item_timeout") or 35))
        return max(DEFAULT_STALE_SECONDS, timeout + 35)
    if phase == "discovering":
        return DISCOVERY_STALE_SECONDS
    if phase in {"cleanup_pre", "cleanup_final"}:
        return CLEANUP_STALE_SECONDS
    if phase == "waiting_lock":
        return max(30, min(DEFAULT_STALE_SECONDS, 90))
    return DEFAULT_STALE_SECONDS


class Command(BaseCommand):
    help = "Durable supervisor for queued Delta source-catalog jobs."

    def _run_job(self, job):
        job_id = job["job_id"]
        SUPERVISOR_DIR.mkdir(parents=True, exist_ok=True)
        log_path = SUPERVISOR_DIR / f"{job_id}.log"
        log_handle = log_path.open("ab")
        process = None
        try:
            close_old_connections()
            process = subprocess.Popen(
                [sys.executable, "manage.py", "source_sync_job", job_id],
                cwd=str(settings.BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
            self.stdout.write(f"Started catalog job {job_id} pid={process.pid}")

            while process.poll() is None:
                time.sleep(2.0)
                close_old_connections()
                current = read_job(job_id)
                if not current:
                    _kill_group(process)
                    return
                if current.get("status") in TERMINAL:
                    # The child should normally be about to exit; give it a brief
                    # grace period and then reap it without leaving an orphan.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _kill_group(process)
                    return

                age = current.get("heartbeat_age")
                limit = _stale_limit(current)
                if age is not None and int(age) > int(limit):
                    _kill_group(process)
                    detail = _tail(log_path)
                    mark_failed(
                        job_id,
                        "sync_worker_stalled",
                        f"پردازش اصلی Sync بیش از {limit} ثانیه heartbeat نداد و در سطح سیستم‌عامل متوقف شد.",
                        worker_log_tail=detail[-2400:] if detail else "",
                    )
                    self.stderr.write(f"Killed stalled catalog job {job_id}; heartbeat age={age}s")
                    return

            close_old_connections()
            current = read_job(job_id)
            if current and current.get("status") in TERMINAL:
                return
            detail = _tail(log_path)
            mark_failed(
                job_id,
                "sync_worker_exited",
                f"پردازش اصلی Sync بدون وضعیت نهایی خارج شد (exit={process.returncode}).",
                worker_log_tail=detail[-2400:] if detail else "",
            )
        except Exception as exc:
            if process and process.poll() is None:
                _kill_group(process)
            mark_failed(job_id, "sync_supervisor_error", str(exc)[:1000], worker_log_tail=_tail(log_path)[-2400:])
        finally:
            try:
                log_handle.close()
            except Exception:
                pass
            try:
                log_path.unlink(missing_ok=True)
            except OSError:
                pass
            close_old_connections()

    def handle(self, *args, **options):
        recovered = recover_orphaned_running_jobs()
        if recovered:
            self.stdout.write(f"Recovered {recovered} orphaned running catalog job(s).")
        prune_old_jobs()
        self.stdout.write("Delta source catalog supervisor v26 is ready.")

        while True:
            close_old_connections()
            job = claim_next_job()
            if not job:
                time.sleep(POLL_SECONDS)
                continue
            self._run_job(job)
