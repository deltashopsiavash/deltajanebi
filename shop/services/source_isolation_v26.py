"""OS-process isolation for every risky source-network/native-code operation."""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from django.conf import settings


TMP_DIR = Path(os.getenv("DELTA_SOURCE_ISOLATION_DIR", "/tmp/deltajanebi-source-sync-v26"))
PRODUCT_WALL_SECONDS = max(12.0, min(float(os.getenv("DELTA_SOURCE_PRODUCT_WALL_TIMEOUT", "35")), 180.0))
DISCOVERY_STALL_SECONDS = max(20.0, min(float(os.getenv("DELTA_SOURCE_DISCOVERY_STALL_TIMEOUT", "45")), 180.0))
DISCOVERY_POLL_SECONDS = max(0.25, min(float(os.getenv("DELTA_SOURCE_DISCOVERY_POLL_SECONDS", "0.75")), 3.0))


def _empty_product(**extra):
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


def kill_process_group(process):
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


def _tail(path, limit=1400):
    try:
        raw = Path(path).read_bytes()
        return raw[-limit:].decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _cleanup(paths):
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def run_product_isolated(site, url):
    """Synchronize exactly one URL in a disposable child with a parent timeout."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    token = f"item-{os.getpid()}-{int(site.pk or 0)}-{uuid.uuid4().hex}"
    result_path = TMP_DIR / f"{token}.json"
    log_path = TMP_DIR / f"{token}.log"
    command = [
        sys.executable,
        "manage.py",
        "source_sync_one",
        str(site.pk),
        str(url),
        str(result_path),
    ]
    process = None
    log_handle = None
    try:
        log_handle = log_path.open("ab")
        process = subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        try:
            process.wait(timeout=PRODUCT_WALL_SECONDS)
        except subprocess.TimeoutExpired:
            kill_process_group(process)
            return _empty_product(
                errors=1,
                warning=(
                    f"{site.name}: product_hard_timeout: محصول پس از "
                    f"{int(PRODUCT_WALL_SECONDS)} ثانیه در سطح سیستم‌عامل متوقف شد؛ Sync ادامه یافت."
                )[:700],
            )

        result = _read_json(result_path)
        if result:
            return _empty_product(**result)
        detail = _tail(log_path)
        return _empty_product(
            errors=1,
            warning=(
                f"{site.name}: پردازش ایزوله محصول بدون نتیجه پایان یافت"
                f" (exit={process.returncode})"
                + (f": {detail}" if detail else "")
            )[:700],
        )
    except Exception as exc:
        if process and process.poll() is None:
            kill_process_group(process)
        return _empty_product(
            errors=1,
            warning=f"{site.name}: شروع/نظارت پردازش ایزوله محصول ناموفق بود: {exc}"[:700],
        )
    finally:
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:
                pass
        _cleanup([result_path, log_path])


def _partial_urls(path):
    try:
        values = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def run_discovery_isolated(
    site,
    *,
    budget_seconds,
    max_sitemaps,
    max_pages,
    progress=None,
    stop_requested=None,
):
    """Discover one source in a killable child and preserve partial URLs."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    token = f"discover-{os.getpid()}-{int(site.pk or 0)}-{uuid.uuid4().hex}"
    result_path = TMP_DIR / f"{token}.result.json"
    progress_path = TMP_DIR / f"{token}.progress.json"
    urls_path = TMP_DIR / f"{token}.urls"
    log_path = TMP_DIR / f"{token}.log"
    command = [
        sys.executable,
        "manage.py",
        "source_discover_one",
        str(site.pk),
        str(result_path),
        str(progress_path),
        str(urls_path),
        str(int(budget_seconds)),
        str(int(max_sitemaps)),
        str(int(max_pages)),
    ]
    started = time.monotonic()
    last_heartbeat = started
    last_progress_mtime = None
    last_info = {
        "phase": "starting",
        "requests": 0,
        "found": 0,
        "elapsed": 0,
        "budget": int(budget_seconds),
        "current_url": "",
    }
    process = None
    log_handle = None
    stalled = False
    hard_timed_out = False
    stopped = False

    try:
        log_handle = log_path.open("ab")
        process = subprocess.Popen(
            command,
            cwd=str(settings.BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )

        hard_deadline = started + max(float(budget_seconds) + 30.0, DISCOVERY_STALL_SECONDS + 15.0)
        while process.poll() is None:
            now = time.monotonic()
            try:
                mtime = progress_path.stat().st_mtime_ns
            except OSError:
                mtime = None
            if mtime is not None and mtime != last_progress_mtime:
                info = _read_json(progress_path)
                if info:
                    last_info.update(info)
                    last_heartbeat = now
                    last_progress_mtime = mtime
                    if progress:
                        try:
                            progress(dict(last_info))
                        except Exception:
                            pass

            if stop_requested:
                try:
                    if stop_requested():
                        stopped = True
                        kill_process_group(process)
                        break
                except Exception:
                    pass
            if now - last_heartbeat > DISCOVERY_STALL_SECONDS:
                stalled = True
                kill_process_group(process)
                break
            if now >= hard_deadline:
                hard_timed_out = True
                kill_process_group(process)
                break
            time.sleep(DISCOVERY_POLL_SECONDS)

        urls = _partial_urls(urls_path)
        result = _read_json(result_path) or {}
        meta = dict(result.get("meta") or {})
        meta.setdefault("requests", int(last_info.get("requests") or 0))
        meta.setdefault("found", len(urls))
        meta.setdefault("elapsed", int(time.monotonic() - started))
        meta.setdefault("budget", int(budget_seconds))
        meta["found"] = len(urls)
        meta["stalled"] = bool(stalled)
        meta["hard_timed_out"] = bool(hard_timed_out)
        meta["stopped"] = bool(stopped)
        meta["worker_exit"] = process.returncode if process else None

        warning = ""
        if stopped:
            warning = f"{site.name}: کشف خودکار برای اولویت دادن به Sync دستی متوقف شد؛ {len(urls)} URL پیدا‌شده حفظ شد."
        elif stalled:
            warning = (
                f"{site.name}: discovery_hard_stall: کشف بیش از {int(DISCOVERY_STALL_SECONDS)} ثانیه "
                f"بدون heartbeat ماند؛ پردازش کشف Kill شد و {len(urls)} URL پیدا‌شده حفظ شد."
            )
        elif hard_timed_out:
            warning = f"{site.name}: discovery_hard_timeout؛ {len(urls)} URL پیدا‌شده حفظ شد."
        elif not result.get("ok", False):
            detail = str(result.get("message") or _tail(log_path) or "discovery worker exited")
            warning = f"{site.name}: discovery worker: {detail[:600]}"
        return urls, meta, warning[:900]
    except Exception as exc:
        if process and process.poll() is None:
            kill_process_group(process)
        urls = _partial_urls(urls_path)
        return urls, {
            "requests": int(last_info.get("requests") or 0),
            "found": len(urls),
            "elapsed": int(time.monotonic() - started),
            "budget": int(budget_seconds),
            "stalled": True,
            "stopped": stopped,
        }, f"{site.name}: discovery supervisor: {exc}"[:900]
    finally:
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:
                pass
        _cleanup([result_path, progress_path, urls_path, log_path])
