import base64
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.models import Banner, Category, Product, SiteSetting, SourceSite, TrustBadge, User
from shop.services.source_catalog import apply_site_markup_to_existing, upsert_source_product_with_changes
from shop.source_registry import registered_source_for_url
from shop.services.wallet import external_payable, order_wallet_info, wallet_balance

from .pricing import pricing_data
from .site_api import _authorized, _decode, _json, _not_found
from .backup import BACKUP_DIR, create_backup_archive, validate_backup_archive
from .site_api_v14 import bot_api as v14_bot_api

MAX_BOT_BACKUP_BYTES = 35 * 1024 * 1024


def _ok(data=None, **extra):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return JsonResponse(payload)


def _public_url(value):
    value = str(value or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("لینک باید با http:// یا https:// شروع شود.")
    return value[:4096]


def _product_detail(item):
    price = pricing_data(item)
    return {
        "id": item.id,
        "name": item.name,
        "sku": item.public_code or item.sku or "",
        "source_type": item.source_type,
        "source_url": item.source_url or "",
        "source_price": int(item.source_price or 0),
        "price": int(item.price or 0),
        "stock": int(item.stock or 0),
        "reserved_stock": int(item.reserved_stock or 0),
        "available_stock": int(item.available_stock),
        "is_active": item.is_active,
        "category_id": item.category_id,
        "category": item.category.name if item.category_id else "",
        "description": item.description or "",
        "primary_image": item.primary_image or "",
        "gallery": list(item.gallery or []),
        "markup_type": item.markup_type,
        "markup_value": str(item.markup_value),
        "manual_overrides": {
            "name": bool(item.manual_name_override),
            "price": item.manual_price_override is not None,
            "stock": item.manual_stock_override is not None,
            "image": bool(item.image or item.manual_image_url_override),
        },
        "last_synced_at": item.last_synced_at.isoformat() if item.last_synced_at else None,
        "sync_error": item.sync_error or "",
        **price,
    }


def _category_descendant_ids(root):
    ids = [root.id]
    frontier = [root.id]
    while frontier:
        children = list(Category.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
        children = [x for x in children if x not in ids]
        if not children:
            break
        ids.extend(children)
        frontier = children
    return ids


def _category_delete_preview(root):
    ids = _category_descendant_ids(root)
    return {
        "id": root.id,
        "name": root.name,
        "category_count": len(ids),
        "descendant_count": max(0, len(ids) - 1),
        "direct_products": Product.objects.filter(category=root).count(),
        "all_products": Product.objects.filter(category_id__in=ids).count(),
        "product_behavior": "keep_without_category",
    }


def _commerce_data():
    store = SiteSetting.load()
    return {
        "card_payment_enabled": store.card_payment_enabled,
        "zarinpal_payment_enabled": store.zarinpal_payment_enabled,
        "card_number": store.card_number,
        "card_owner": store.card_owner,
        "zarinpal_merchant_id": store.zarinpal_merchant_id,
        "shipping_cost": int(store.shipping_cost or 0),
        "packaging_cost": int(store.packaging_cost or 0),
        "free_shipping_threshold": int(store.free_shipping_threshold or 0),
        "hide_out_of_stock": store.hide_out_of_stock,
        "terms_text": store.terms_text or "",
    }


def _badge_row(item):
    return {
        "id": item.id,
        "badge_type": item.badge_type,
        "label": item.get_badge_type_display(),
        "is_active": item.is_active,
        "has_image": bool(item.image_src),
        "target_url": item.target_url or "",
    }


def _save_field_unique(field, content, source_name, prefix):
    ext = Path(str(source_name or "")).suffix.lower()
    if not ext or len(ext) > 8:
        ext = ".jpg"
    field.save(f"{prefix}-{uuid.uuid4().hex}{ext}", content, save=False)


def _delete_field_file(field):
    if not field:
        return
    try:
        field.delete(save=False)
    except Exception:
        pass


def _category_by_path(path):
    parent = None
    for raw in path or []:
        name = str(raw or "").strip()[:120]
        if not name:
            continue
        item = Category.objects.filter(parent=parent, name=name).first()
        if not item:
            item = Category.objects.create(parent=parent, name=name, slug="")
        parent = item
    return parent


def _source_for_payload(payload):
    source = SourceSite.objects.filter(pk=payload.get("source_id"), is_active=True).first()
    if not source:
        raise ValueError("سایت منبع پیدا نشد یا غیرفعال است.")
    url = str(payload.get("url") or "").strip()
    registered = registered_source_for_url(url, active_only=True)
    if not registered or registered.id != source.id:
        raise ValueError("این لینک متعلق به سایت منبع انتخاب‌شده نیست.")
    return source, url


def _set_product_url_image(item, url):
    value = _public_url(url)
    _delete_field_file(item.image)
    item.image = ""
    if item.source_type == Product.SYNCED:
        item.manual_image_url_override = value
        item.save(update_fields=["image", "manual_image_url_override", "updated_at"])
    else:
        item.image_url = value
        item.save(update_fields=["image", "image_url", "updated_at"])


def _clear_product_image(item):
    _delete_field_file(item.image)
    item.image = ""
    if item.source_type == Product.SYNCED:
        item.manual_image_url_override = ""
        item.save(update_fields=["image", "manual_image_url_override", "updated_at"])
    else:
        item.image_url = ""
        item.gallery = []
        item.save(update_fields=["image", "image_url", "gallery", "updated_at"])


def _parse_positive(value, field):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field}_invalid")
    if number < 0:
        raise ValueError(f"{field}_invalid")
    return number


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}

    try:
        if action == "ping":
            store = SiteSetting.load()
            return _ok(None, site={
                "name": store.store_name,
                "domain": os.environ.get("DOMAIN", ""),
                "version": 15,
                "platform": "deltajanebi",
                "capabilities": [
                    "delta_native_panel", "manual_products", "source_products", "source_sites",
                    "bulk_sync", "catalog_purge", "wallet", "announcements", "commerce",
                    "discount_price", "timed_offer", "amazing_price", "product_stories",
                    "full_backup", "users", "broadcast_email", "orders", "receipts",
                ],
            })

        if action == "delta_product_detail":
            item = Product.objects.select_related("category").filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            return _ok(_product_detail(item))

        if action == "delta_manual_product_create":
            name = str(payload.get("name") or "").strip()[:300]
            price = _parse_positive(payload.get("price"), "price")
            stock = _parse_positive(payload.get("stock"), "stock")
            if not name or price <= 0:
                return JsonResponse({"ok": False, "error": "invalid_product"}, status=400)
            category = _category_by_path(payload.get("category_path") or [])
            item = Product.objects.create(category=category, name=name, price=price, stock=stock, source_type=Product.MANUAL)
            if payload.get("image_url"):
                _set_product_url_image(item, payload["image_url"])
            elif payload.get("image_b64"):
                name2, content = _decode(payload)
                _save_field_unique(item.image, content, name2, "manual-product")
                item.save(update_fields=["image", "updated_at"])
            return _ok(_product_detail(item))

        if action == "delta_product_from_source":
            source, url = _source_for_payload(payload)
            mt = str(payload.get("markup_type") or source.default_markup_type)
            if mt not in dict(SourceSite.MARKUP_CHOICES):
                return JsonResponse({"ok": False, "error": "invalid_markup_type"}, status=400)
            mv = payload.get("markup_value") if "markup_value" in payload else source.default_markup_value
            product, created, changes = upsert_source_product_with_changes(source, url)
            product.markup_type = mt
            product.markup_value = mv or 0
            if product.source_price:
                product.price = product.apply_markup(product.source_price)
            product.save(update_fields=["markup_type", "markup_value", "price", "updated_at"])
            return _ok({"product": _product_detail(product), "created": created, "changes": changes})

        if action == "delta_product_sync":
            item = Product.objects.filter(pk=payload.get("id"), source_type=Product.SYNCED).first()
            if not item: return _not_found("synced_product")
            source = registered_source_for_url(item.source_url, active_only=True)
            if not source: return JsonResponse({"ok": False, "error": "source_site_not_registered_or_inactive"}, status=409)
            product, created, changes = upsert_source_product_with_changes(source, item.source_url)
            return _ok({"product": _product_detail(product), "created": created, "changes": changes})

        if action == "delta_product_reset_sync":
            item = Product.objects.filter(pk=payload.get("id"), source_type=Product.SYNCED).first()
            if not item: return _not_found("synced_product")
            item.manual_name_override = ""
            item.manual_price_override = None
            item.manual_stock_override = None
            item.manual_image_url_override = ""
            _delete_field_file(item.image)
            item.image = ""
            item.save(update_fields=["manual_name_override", "manual_price_override", "manual_stock_override", "manual_image_url_override", "image", "updated_at"])
            source = registered_source_for_url(item.source_url, active_only=True)
            if not source: return JsonResponse({"ok": False, "error": "source_site_not_registered_or_inactive"}, status=409)
            product, _, _ = upsert_source_product_with_changes(source, item.source_url)
            return _ok(_product_detail(product))

        if action == "delta_product_delete":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            _delete_field_file(item.image)
            item.delete()
            return _ok({"deleted": True})

        if action == "delta_product_image_url_set":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            _set_product_url_image(item, payload.get("url"))
            return _ok(_product_detail(item))

        if action == "delta_product_image_remove":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            _clear_product_image(item)
            return _ok(_product_detail(item))

        if action == "delta_timed_offer_set":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            price = int(payload.get("price") or 0)
            minutes = int(payload.get("minutes") or 0)
            if price <= 0 or price >= int(item.price or 0) or minutes <= 0 or minutes > 43200:
                return JsonResponse({"ok": False, "error": "invalid_timed_offer"}, status=400)
            item.sale_price = price
            item.sale_starts_at = timezone.now()
            item.sale_ends_at = timezone.now() + timedelta(minutes=minutes)
            item.save(update_fields=["sale_price", "sale_starts_at", "sale_ends_at", "updated_at"])
            return _ok(_product_detail(item))

        if action == "delta_timed_offer_clear":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            item.clear_sale()
            return _ok(_product_detail(item))

        if action == "delta_category_delete_preview":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            return _ok(_category_delete_preview(item))

        if action == "delta_category_delete":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            preview = _category_delete_preview(item)
            ids = _category_descendant_ids(item)
            # Product.category is SET_NULL: native Delta behavior preserves products.
            Product.objects.filter(category_id__in=ids).update(category=None)
            Category.objects.filter(id__in=ids).delete()
            preview["deleted"] = True
            return _ok(preview)

        if action == "delta_category_image_url_set":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            item.image_url = _public_url(payload.get("url"))
            item.save(update_fields=["image_url"])
            return _ok({"id": item.id, "image_url": item.image_url})

        if action == "delta_source_markup_update":
            item = SourceSite.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("source_site")
            mt = str(payload.get("markup_type") or item.default_markup_type)
            if mt not in dict(SourceSite.MARKUP_CHOICES):
                return JsonResponse({"ok": False, "error": "invalid_markup_type"}, status=400)
            item.default_markup_type = mt
            item.default_markup_value = payload.get("markup_value") or 0
            item.save(update_fields=["default_markup_type", "default_markup_value"])
            changed = apply_site_markup_to_existing(item) if payload.get("apply_existing", True) else 0
            return _ok({"id": item.id, "markup_label": item.markup_label(), "updated_products": changed})

        if action == "delta_commerce_get":
            return _ok(_commerce_data())

        if action == "delta_commerce_update":
            store = SiteSetting.load()
            bool_fields = {"card_payment_enabled", "zarinpal_payment_enabled", "hide_out_of_stock"}
            int_fields = {"shipping_cost", "packaging_cost", "free_shipping_threshold"}
            text_fields = {"card_number", "card_owner", "zarinpal_merchant_id", "terms_text"}
            changed = []
            for field in bool_fields:
                if field in payload: setattr(store, field, bool(payload[field])); changed.append(field)
            for field in int_fields:
                if field in payload: setattr(store, field, max(0, int(payload[field] or 0))); changed.append(field)
            for field in text_fields:
                if field in payload: setattr(store, field, str(payload[field] or "").strip()); changed.append(field)
            if changed: store.save(update_fields=changed)
            return _ok(_commerce_data())

        if action == "delta_user_detail":
            user = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not user: return _not_found("user")
            from .site_api import _user_detail
            data2 = _user_detail(user)
            data2["wallet_balance"] = wallet_balance(user.id)
            return _ok(data2)

        if action == "delta_order_wallet":
            from shop.models import Order
            order = Order.objects.filter(pk=payload.get("id")).first()
            if not order: return _not_found("order")
            info = order_wallet_info(order.id); info["external_payable"] = external_payable(order)
            return _ok(info)

        if action == "backup_create":
            path = create_backup_archive(str(payload.get("label") or "manual")[:24])
            validate_backup_archive(path)
            backups = sorted(BACKUP_DIR.glob("deltajanebi-full-*.deltabackup"), reverse=True)
            for old in backups[10:]:
                old.unlink(missing_ok=True)
            size = path.stat().st_size
            from .models import AddonSetting
            addon = AddonSetting.load(); addon.last_backup_at = timezone.now(); addon.save(update_fields=["last_backup_at", "updated_at"])
            if size > MAX_BOT_BACKUP_BYTES:
                return JsonResponse({
                    "ok": False,
                    "error": "backup_too_large",
                    "detail": "بکاپ کامل روی سرور ذخیره شد اما برای ارسال مستقیم در تلگرام بزرگ است؛ از بخش بکاپ‌های سرور دریافت/مدیریت کنید.",
                    "data": {"filename": path.name, "size": size, "too_large": True, "stored_on_server": True},
                }, status=413)
            raw = path.read_bytes()
            return _ok({"filename": path.name, "size": size, "too_large": False, "stored_on_server": True, "backup_b64": base64.b64encode(raw).decode("ascii")})

        if action == "delta_backup_list":
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            rows = []
            for path in sorted(BACKUP_DIR.glob("deltajanebi-full-*.deltabackup"), reverse=True)[:10]:
                rows.append({"filename": path.name, "size": path.stat().st_size, "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone()).isoformat()})
            return _ok(rows)

        if action == "delta_backup_get":
            name = os.path.basename(str(payload.get("filename") or ""))
            if not name.endswith(".deltabackup"):
                return JsonResponse({"ok": False, "error": "invalid_backup_filename"}, status=400)
            path = BACKUP_DIR / name
            if not path.is_file():
                return _not_found("backup")
            validate_backup_archive(path)
            size = path.stat().st_size
            if size > MAX_BOT_BACKUP_BYTES:
                return JsonResponse({
                    "ok": False,
                    "error": "backup_too_large",
                    "detail": "این بکاپ روی سرور موجود است ولی برای انتقال از API/تلگرام بزرگ است.",
                    "data": {"filename": name, "size": size, "too_large": True, "stored_on_server": True},
                }, status=413)
            raw = path.read_bytes()
            return _ok({"filename": name, "size": size, "too_large": False, "backup_b64": base64.b64encode(raw).decode("ascii")})

        if action == "delta_badges":
            return _ok([_badge_row(x) for x in TrustBadge.objects.order_by("id")])

        if action == "delta_badge_set":
            kind = str(payload.get("badge_type") or "").strip()
            if kind not in dict(TrustBadge.TYPE_CHOICES): return JsonResponse({"ok": False, "error": "invalid_badge_type"}, status=400)
            item, _ = TrustBadge.objects.get_or_create(badge_type=kind)
            if payload.get("target_url") is not None:
                item.target_url = _public_url(payload["target_url"]) if str(payload["target_url"]).strip() else ""
            if payload.get("image_url"):
                if item.image:
                    try: item.image.delete(save=False)
                    except Exception: pass
                item.image = ""; item.image_url = _public_url(payload["image_url"])
            if payload.get("image_b64"):
                name, content = _decode(payload)
                if item.image:
                    try: item.image.delete(save=False)
                    except Exception: pass
                _save_field_unique(item.image, content, name, f"badge-{kind}"); item.image_url = ""
            item.is_active = bool(payload.get("is_active", True)); item.save()
            return _ok(_badge_row(item))

        if action == "delta_badge_remove":
            item = TrustBadge.objects.filter(badge_type=str(payload.get("badge_type") or "")).first()
            if not item: return _ok({"deleted": False})
            _delete_field_file(item.image); item.delete(); return _ok({"deleted": True})

        if action == "delta_logo_url_set":
            store = SiteSetting.load(); _delete_field_file(store.logo); store.logo = ""; store.logo_url = _public_url(payload.get("url")); store.save(update_fields=["logo", "logo_url"]); return _ok({"logo_url": store.logo_url})

        if action == "delta_banner_create":
            title = str(payload.get("title") or "")[:160]
            target = str(payload.get("target_url") or "").strip()
            if target: target = _public_url(target)
            x = Banner(title=title, target_url=target, is_active=True)
            if payload.get("image_url"):
                x.image_url = _public_url(payload["image_url"])
            elif payload.get("image_b64"):
                name, content = _decode(payload)
                _save_field_unique(x.image, content, name, "banner-desktop")
            else:
                return JsonResponse({"ok": False, "error": "banner_image_required"}, status=400)
            x.save(); return _ok({"id": x.id})

        if action == "delta_banner_media_set":
            x = Banner.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("banner")
            mobile = bool(payload.get("mobile"))
            field = x.mobile_image if mobile else x.image
            url_field = "mobile_image_url" if mobile else "image_url"
            if payload.get("image_url"):
                _delete_field_file(field); setattr(x, field.field.name, ""); setattr(x, url_field, _public_url(payload["image_url"]))
            elif payload.get("image_b64"):
                name, content = _decode(payload); _delete_field_file(field); _save_field_unique(field, content, name, "banner-mobile" if mobile else "banner-desktop"); setattr(x, url_field, "")
            else:
                return JsonResponse({"ok": False, "error": "image_required"}, status=400)
            x.save(); return _ok({"id": x.id})

        return v14_bot_api(request)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": "delta_v15_action_failed", "detail": str(exc)[:700]}, status=500)
