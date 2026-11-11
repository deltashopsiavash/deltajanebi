"""Safe superuser-only browser restore for archives above Telegram Bot API's 20MB limit."""
from pathlib import Path
import tempfile
import zipfile

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from .backup import BACKUP_DIR, restore_backup_archive, validate_backup_archive

MAX_BROWSER_BACKUP_BYTES = 150 * 1024 * 1024


@staff_member_required(login_url="admin:login")
@csrf_protect
@require_http_methods(["GET", "POST"])
def delta_backup_restore_admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only a superuser can restore a full-site backup")
    state = {"title": "بازگردانی بکاپ کامل DeltaJanebi", "max_mb": 150}
    if request.method == "GET":
        return render(request, "admin/delta_backup_restore.html", state)

    uploaded = request.FILES.get("backup_file")
    if not uploaded or not str(uploaded.name).lower().endswith(".deltabackup"):
        state["error"] = "فایل بکاپ با پسوند .deltabackup انتخاب کن."
        return render(request, "admin/delta_backup_restore.html", state, status=400)
    if request.POST.get("confirm_restore") != "yes":
        state["error"] = "برای جایگزینی داده‌های فعلی باید تأیید صریح را انتخاب کنی."
        return render(request, "admin/delta_backup_restore.html", state, status=400)
    if uploaded.size and uploaded.size > MAX_BROWSER_BACKUP_BYTES:
        state["error"] = "حجم این بکاپ از ۱۵۰ مگابایت بیشتر است."
        return render(request, "admin/delta_backup_restore.html", state, status=413)

    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=".delta-admin-upload-",
                                         suffix=".deltabackup", dir=BACKUP_DIR, delete=False) as f:
            path = Path(f.name)
            received = 0
            for chunk in uploaded.chunks():
                received += len(chunk)
                if received > MAX_BROWSER_BACKUP_BYTES:
                    raise ValueError("حجم این بکاپ از ۱۵۰ مگابایت بیشتر است.")
                f.write(chunk)

        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 30000 or sum(item.file_size for item in entries) > 2 * 1024**3:
                raise ValueError("بکاپ بیش از حد بزرگ یا دارای ساختار غیرعادی است.")

        manifest = validate_backup_archive(path)
        emergency = restore_backup_archive(path)
        state["success"] = True
        state["created_at"] = manifest.get("created_at", "")
        state["emergency_name"] = emergency.name if emergency else ""
        return render(request, "admin/delta_backup_restore.html", state)
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        state["error"] = str(exc)[:450] or "خواندن بکاپ ناموفق بود."
        return render(request, "admin/delta_backup_restore.html", state, status=400)
    except Exception:
        state["error"] = "بازگردانی انجام نشد. لاگ سرور را بررسی کن؛ بکاپ اضطراری را حذف نکن."
        return render(request, "admin/delta_backup_restore.html", state, status=500)
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
