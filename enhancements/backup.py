import hashlib
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

BACKUP_DIR = Path(settings.BASE_DIR) / "backups"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {2}
DATABASE_EXCLUDES = ["contenttypes", "auth.Permission"]


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_model_counts():
    counts = {}
    excluded = {"contenttypes.contenttype", "auth.permission"}
    for model in apps.get_models():
        label = model._meta.label_lower
        if label in excluded:
            continue
        try:
            counts[label] = model._default_manager.count()
        except Exception:
            continue
    return counts


def _collect_media_manifest(media_root):
    files, total_bytes = [], 0
    if not media_root.exists():
        return files, total_bytes
    for item in sorted(media_root.rglob("*")):
        if not item.is_file() or item.name == ".restore-in-progress":
            continue
        relative = item.relative_to(media_root).as_posix()
        size = item.stat().st_size
        files.append({"path": relative, "size": size, "sha256": _sha256_file(item)})
        total_bytes += size
    return files, total_bytes


def validate_backup_archive(path):
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ValueError("فایل، بکاپ معتبر Delta Janebi نیست.")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if not {"manifest.json", "database.json"}.issubset(names):
            raise ValueError("فایل‌های اصلی بکاپ وجود ندارند.")
        for name in names:
            item = PurePosixPath(name)
            if item.is_absolute() or ".." in item.parts:
                raise ValueError("ساختار فایل بکاپ ناامن است.")
        manifest = json.loads(archive.read("manifest.json"))
        version = int(manifest.get("schema_version") or 0)
        if manifest.get("format") != "deltajanebi-backup" or version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("نسخه این بکاپ با Delta Janebi سازگار نیست.")
        database_raw = archive.read("database.json")
        json.loads(database_raw)
        if manifest.get("database_sha256") != _sha256_bytes(database_raw):
            raise ValueError("کنترل صحت دیتابیس بکاپ ناموفق بود.")
        for item in manifest.get("media_files") or []:
            relative = str(item.get("path") or "")
            member = f"media/{relative}"
            if not relative or member not in names:
                raise ValueError("یکی از فایل‌های media بکاپ ناقص است.")
            raw = archive.read(member)
            if len(raw) != int(item.get("size") or 0) or _sha256_bytes(raw) != item.get("sha256"):
                raise ValueError("کنترل صحت یکی از فایل‌های media ناموفق بود.")
    return manifest


def create_backup_archive(label="auto"):
    """Exact store-state backup: all application DB rows + every MEDIA_ROOT file.

    Source, static output and deployment secrets are intentionally excluded: source is restored
    from Git, static is regenerated, and .env/API keys/passwords must never travel through Telegram.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    output = BACKUP_DIR / f"deltajanebi-full-{label}-{stamp}.deltabackup"

    database = io.StringIO()
    call_command("dumpdata", exclude=DATABASE_EXCLUDES, natural_foreign=True, natural_primary=True, indent=2, stdout=database)
    database_raw = database.getvalue().encode("utf-8")
    media_root = Path(settings.MEDIA_ROOT)
    media_files, media_bytes = _collect_media_manifest(media_root)
    model_counts = _database_model_counts()
    manifest = {
        "format": "deltajanebi-backup",
        "schema_version": SCHEMA_VERSION,
        "backup_kind": "full-site",
        "created_at": timezone.now().isoformat(),
        "includes": [
            "all_application_database_rows",
            "users_customer_codes_wallet_and_permissions",
            "products_sources_sync_overrides_and_categories",
            "ordinary_discounts_and_amazing_prices",
            "orders_items_receipts_reservations_and_payment_state",
            "wallet_transactions_and_wallet_order_payments",
            "discount_codes",
            "site_payment_footer_social_banner_and_trust_settings",
            "announcements_and_reads",
            "product_stories",
            "email_verification_state",
            "external_bot_event_state",
            "all_uploaded_media",
        ],
        "database_excludes": DATABASE_EXCLUDES,
        "database_sha256": _sha256_bytes(database_raw),
        "database_objects": sum(model_counts.values()),
        "model_counts": model_counts,
        "media_file_count": len(media_files),
        "media_bytes": media_bytes,
        "media_files": media_files,
        "deployment_secrets_included": False,
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("database.json", database_raw)
        for item in media_files:
            source = media_root / Path(*PurePosixPath(item["path"]).parts)
            archive.write(source, f"media/{item['path']}")
    validate_backup_archive(output)
    return output


def _load_database_json(data):
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as handle:
        handle.write(data)
        fixture = Path(handle.name)
    try:
        for connection in connections.all():
            connection.close()
        call_command("flush", interactive=False, verbosity=0)
        call_command("loaddata", str(fixture), verbosity=0)
    finally:
        fixture.unlink(missing_ok=True)


def _clear_media(media_root):
    media_root.mkdir(parents=True, exist_ok=True)
    for item in list(media_root.iterdir()):
        if item.name == ".restore-in-progress":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink(missing_ok=True)


def _restore_media(archive, replace=True):
    media_root = Path(settings.MEDIA_ROOT).resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    if replace:
        _clear_media(media_root)
    for name in archive.namelist():
        if not name.startswith("media/") or name.endswith("/"):
            continue
        relative = PurePosixPath(name).relative_to("media")
        target = (media_root / Path(*relative.parts)).resolve()
        if target != media_root and media_root not in target.parents:
            raise ValueError("مسیر رسانه‌ای نامعتبر است.")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(name) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def restore_backup_archive(path):
    validate_backup_archive(path)
    emergency = create_backup_archive("before-restore")
    marker = Path(settings.MEDIA_ROOT) / ".restore-in-progress"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(timezone.now().isoformat(), encoding="utf-8")
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                _load_database_json(archive.read("database.json"))
                _restore_media(archive, replace=True)
            except Exception:
                with zipfile.ZipFile(emergency) as rollback:
                    _load_database_json(rollback.read("database.json"))
                    _restore_media(rollback, replace=True)
                raise
    finally:
        marker.unlink(missing_ok=True)
    return emergency
