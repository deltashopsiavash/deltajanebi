"""Full catalog sync v25.

Manual catalog products are executed in separate OS processes. Python SIGALRM is
not enough when code is blocked inside a C extension such as Pillow/lxml, libc
DNS, SSL or another native call: the Python signal handler may not run until the
native function returns. The parent now owns the deadline and can SIGKILL the
single-product child, guaranteeing that one bad URL cannot freeze the full job.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

from django.conf import settings

# Import the v22 catalog bridge before v21 bulk so all legacy call sites have the
# same identity/category behavior in this process.
from shop.services import source_catalog_v22 as catalog
from shop.services import source_bulk_job_v21 as base
from shop.services import category_v22
from shop.services import source_identity_v22 as identity
from shop.services.source_sync_lock import catalog_sync_lock

base.upsert_source_product_with_changes = catalog.upsert_source_product_with_changes
base.import_unpriced_catalog_product = catalog.import_unpriced_catalog_product
base.CatalogSkip = catalog.CatalogSkip
base.consolidate_sibling_duplicates = category_v22.consolidate_sibling_duplicates
base.consolidate_duplicate_products = identity.consolidate_duplicate_products
base.aggregate_product = identity.aggregate_product

# Discovery keeps a generous catalog-wide allowance, while each individual
# product below has its own much smaller hard deadline.
base.DISCOVERY_BUDGET = max(300, int(os.getenv("DELTA_SOURCE_DISCOVERY_BUDGET", "1800")))
base.DISCOVERY_MAX_SITEMAPS = max(250, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_SITEMAPS", "2000")))
base.DISCOVERY_MAX_PAGES = max(300, int(os.getenv("DELTA_SOURCE_DISCOVERY_MAX_PAGES", "5000")))
base.PRODUCT_DELAY = max(0.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_DELAY", "0.02")), 1.0))
PRODUCT_WALL_SECONDS = max(12.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_WALL_TIMEOUT", "35")), 180.0))


class ProductDeadlineExceeded(TimeoutError):
    pass


@contextmanager
def _product_deadline():
    """Legacy in-process watchdog retained for non-manual callers."""
    supported = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not supported:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def handler(_signum, _frame):
        raise ProductDeadlineExceeded(
            f"product_sync_timeout: سقف {int(PRODUCT_WALL_SECONDS)} ثانیه برای این محصول تمام شد"
        )

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, PRODUCT_WALL_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _empty_result(**extra):
    data = {
        "created": 0,
        "changed": 0,
        "skipped": 0,
        "errors": 0,
        "product_id": 0,
        "changes": {},
        "warning": "",
    }
    data.update(extra)
    return data


def _kill_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except OSError:
            pass


def run_product_isolated(site, url):
    """Run one product in a disposable child with a parent-enforced deadline.

    This is a *real* wall-clock timeout: even if the child is stuck inside native
    code and cannot execute Python's SIGALRM handler, the parent can terminate the
    whole child process group. The child writes its result atomically to /tmp.
    """
    base.JOB_DIR.mkdir(parents=True, exist_ok=True)
    result_path = base.JOB_DIR / (
        f".item-{os.getpid()}-{int(site.pk or 0)}-{uuid.uuid4().hex}.json"
    )
    command = [
        sys.executable,
        "manage.py",
        "source_sync_one",
        str(site.pk),
        str(url),
        str(result_path),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        return _empty_result(
            errors=1,
            warning=f"{site.name}: شروع پردازش ایزوله محصول ناموفق بود: {exc}"[:700],
        )

    stderr = b""
    try:
        _, stderr = process.communicate(timeout=PRODUCT_WALL_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        try:
            _, stderr = process.communicate(timeout=5)
        except Exception:
            pass
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        return _empty_result(
            errors=1,
            warning=(
                f"{site.name}: product_hard_timeout: محصول پس از "
                f"{int(PRODUCT_WALL_SECONDS)} ثانیه در سطح سیستم‌عامل متوقف شد و Sync ادامه یافت."
            )[:700],
        )

    try:
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if isinstance(result, dict):
                return _empty_result(**result)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    finally:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass

    detail = (stderr or b"")[-1400:].decode("utf-8", errors="replace").strip()
    return _empty_result(
        errors=1,
        warning=(
            f"{site.name}: پردازش ایزوله محصول بدون نتیجه پایان یافت"
            + (f" (exit={process.returncode})" if process.returncode is not None else "")
            + (f": {detail}" if detail else "")
        )[:700],
    )


def _sync_urls(job_id, state, site, urls, phase):
    urls = list(urls or [])
    state["phase"] = phase
    state["phase_total"] = len(urls)
    state["phase_checked"] = 0
    state["current_site"] = site.name
    state["item_started_at"] = 0
    state["item_timeout"] = int(PRODUCT_WALL_SECONDS)
    state["item_mode"] = "isolated_process"
    base.write_job(job_id, state)

    for url in urls:
        state["current_url"] = str(url)[:500]
        state["item_started_at"] = time.time()
        state["item_timeout"] = int(PRODUCT_WALL_SECONDS)
        state["item_mode"] = "isolated_process"
        base.write_job(job_id, state)

        result = run_product_isolated(site, url)
        state["created"] += int(result.get("created") or 0)
        state["changed"] += int(result.get("changed") or 0)
        state["skipped"] += int(result.get("skipped") or 0)
        state["errors"] += int(result.get("errors") or 0)
        warning = str(result.get("warning") or "").strip()
        if warning:
            base._warn(state, warning)

        state["checked"] += 1
        state["phase_checked"] += 1
        state["item_started_at"] = 0
        base.write_job(job_id, state)
        if base.PRODUCT_DELAY:
            time.sleep(base.PRODUCT_DELAY)


base._sync_urls = _sync_urls
_base_run_full_sync = base.run_full_sync


def run_full_sync(job_id):
    """Run one manual full sync only when no other catalog writer is active."""
    with catalog_sync_lock() as acquired:
        if not acquired:
            return base.write_job(job_id, {
                "status": "failed",
                "phase": "failed",
                "error": "another_sync_is_running",
                "message": "همگام‌سازی خودکار یا دستی دیگری در حال اجراست؛ این اجرا شروع نشد.",
            })
        return _base_run_full_sync(job_id)
