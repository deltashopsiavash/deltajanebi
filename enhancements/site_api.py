import base64
import json
import os
import secrets
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import validate_email
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.forms import normalize_mobile
from shop.models import Banner, Category, DiscountCode, Order, Product, SiteSetting, SocialLink, TrustBadge, User
from shop.services.order_workflow import email_customer, mark_paid, release_order_stock

from .backup import create_backup_archive, restore_backup_archive, validate_backup_archive
from .emailing import send_broadcast_email, send_password_reset_email
from .models import AddonSetting, BotEvent, ProductStory
from .pricing import normalize_prices, pricing_data, set_amazing_price, set_discount_price

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_STORY_BYTES = 48 * 1024 * 1024
MAX_BOT_BACKUP_BYTES = 35 * 1024 * 1024

DELTA_TO_API_STATUS = {
    "payment_pending": "pending",
    "receipt_pending": "review",
    "payment_rejected": "rejected",
    "preparing": "processing",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
}
API_TO_DELTA_STATUS = {
    "pending": "payment_pending",
    "review": "receipt_pending",
    "rejected": "payment_rejected",
    "paid": "preparing",
    "processing": "preparing",
    "shipped": "shipped",
    "delivered": "delivered",
    "cancelled": "cancelled",
    **{key: key for key, _ in Order.STATUS},
}


def _json(request):
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return {}


def _unauthorized():
    return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)


def _authorized(request):
    expected = (os.environ.get("DELTAJANEBI_BOT_API_KEY") or os.environ.get("SANASHOP_BOT_API_KEY") or "").strip()
    supplied = request.headers.get("Authorization", "")
    return bool(expected and supplied.startswith("Bearer ") and secrets.compare_digest(supplied[7:].strip(), expected))


def _not_found(name):
    return JsonResponse({"ok": False, "error": f"{name}_not_found"}, status=404)


def _decode(payload, key="image_b64", max_bytes=MAX_IMAGE_BYTES, default_name="upload.jpg"):
    raw = str(payload.get(key) or "")
    if not raw:
        raise ValueError(f"{key}_required")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("invalid_base64") from exc
    if not decoded or len(decoded) > max_bytes:
        raise ValueError("invalid_file_size")
    filename_key = key.replace("_b64", "_filename")
    name = os.path.basename(str(payload.get(filename_key) or payload.get("filename") or default_name))[-140:]
    return name, ContentFile(decoded)


def _field_b64(field, max_bytes=MAX_IMAGE_BYTES):
    if not field:
        raise ValueError("image_not_found")
    with field.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("image_too_large")
    return base64.b64encode(raw).decode("ascii")


def _category_image_set(item, payload):
    name, content = _decode(payload)
    ext = Path(name).suffix.lower() or ".jpg"
    stored = default_storage.save(f"categories/{uuid.uuid4().hex}{ext}", content)
    item.image_url = settings.MEDIA_URL.rstrip("/") + "/" + stored
    item.save(update_fields=["image_url"])


def _status_label(order):
    return order.get_status_display()


def _api_status(order):
    return DELTA_TO_API_STATUS.get(order.status, order.status)


def _order_data(order):
    items = []
    for row in order.items.select_related("product").all():
        items.append({
            "id": row.id,
            "title": row.title,
            "quantity": row.quantity,
            "price": row.price,
            "total": row.total,
            "product_id": row.product_id,
        })
    return {
        "id": order.id,
        "code": str(order.id),
        "full_name": order.full_name,
        "mobile": order.phone,
        "phone": order.phone,
        "province": order.province,
        "city": order.city,
        "address": order.address,
        "postal_code": order.postal_code,
        "subtotal": order.subtotal,
        "discount_amount": order.discount_amount,
        "shipping": order.shipping_cost + order.packaging_cost,
        "total": order.total,
        "status": _api_status(order),
        "native_status": order.status,
        "status_label": _status_label(order),
        "payment_method": order.payment_method,
        "payment_method_label": order.get_payment_method_display(),
        "payment_status": order.payment_status,
        "tracking_code": order.tracking_code,
        "receipt_id": order.id if order.receipt else None,
        "receipt_status": "approved" if order.payment_status == Order.PAY_PAID else ("rejected" if order.payment_status == Order.PAY_REJECTED else "pending"),
        "receipt_rejection_reason": order.receipt_rejection_reason,
        "reservation_active": order.reservation_active,
        "reservation_remaining_seconds": order.reservation_remaining_seconds,
        "stock_committed": order.stock_committed,
        "customer_code": order.user.customer_code if order.user_id else "",
        "customer_phone": order.user.phone if order.user_id else "",
        "customer_user_id": order.user_id,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "items": items,
    }


def _user_row(user):
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name(),
        "customer_code": user.customer_code or "",
        "phone": user.phone or "",
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat() if user.date_joined else None,
        "order_count": user.orders.count(),
    }


def _user_detail(user):
    orders = user.orders.prefetch_related("items").order_by("-created_at")
    paid = orders.filter(payment_status=Order.PAY_PAID)
    recent = [{
        "id": item.id,
        "code": str(item.id),
        "status": _api_status(item),
        "status_label": item.get_status_display(),
        "total": item.total,
        "payment_method": item.payment_method,
        "payment_method_label": item.get_payment_method_display(),
        "tracking_code": item.tracking_code,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "items_count": sum(row.quantity for row in item.items.all()),
    } for item in orders[:30]]
    data = _user_row(user)
    data.update({
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "active_orders": orders.exclude(status__in=["delivered", "cancelled"]).count(),
        "completed_orders": orders.filter(status="delivered").count(),
        "cancelled_orders": orders.filter(status="cancelled").count(),
        "total_spent": paid.aggregate(total=Sum("total"))["total"] or 0,
        "orders": recent,
    })
    try:
        from shop.services.wallet import wallet_balance
        data["wallet_balance"] = wallet_balance(user)
    except Exception:
        data["wallet_balance"] = 0
    return data


def _settings_data():
    store = SiteSetting.load()
    enamad = TrustBadge.objects.filter(badge_type=TrustBadge.ENAMAD).first()
    return {
        "site_name": store.store_name,
        "announcement": store.top_bar_text,
        "shipping_fee": store.shipping_cost,
        "packaging_cost": store.packaging_cost,
        "free_shipping_threshold": store.free_shipping_threshold,
        "card_number": store.card_number,
        "card_owner": store.card_owner,
        "has_logo": bool(store.logo_src),
        "address": store.address,
        "phone": store.footer_phone or store.phone,
        "contact_phone": store.phone,
        "contact_email": store.contact_email,
        "footer_description": store.footer_description,
        "support_text": store.support_text,
        "terms_text": store.terms_text,
        "has_terms": bool((store.terms_text or "").strip()),
        "has_enamad_image": bool(enamad and enamad.image_src),
        "enamad_url": enamad.target_url if enamad else "",
    }


def _story_data(item):
    return {
        "id": item.id,
        "title": item.title,
        "media_type": item.media_type,
        "target_url": item.target_url,
        "is_active": item.is_active,
        "active_now": item.active_now,
        "remaining_seconds": item.remaining_seconds,
        "expires_at": item.expires_at.isoformat(),
        "created_at": item.created_at.isoformat(),
    }


def _backup_status():
    addon = AddonSetting.load()
    interval = int(addon.backup_interval_minutes or 0)
    next_at = addon.last_backup_at + timedelta(minutes=interval) if interval and addon.last_backup_at else None
    return {
        "interval_minutes": interval,
        "last_backup_at": addon.last_backup_at.isoformat() if addon.last_backup_at else None,
        "next_backup_at": next_at.isoformat() if next_at else None,
        "due": bool(interval and (next_at is None or timezone.now() >= next_at)),
        "max_bot_bytes": MAX_BOT_BACKUP_BYTES,
    }


@csrf_exempt
def bot_api(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)
    if not _authorized(request):
        return _unauthorized()

    data = _json(request)
    action = str(data.get("action") or "")
    payload = data.get("payload") or {}
    store = SiteSetting.load()

    try:
        if action == "ping":
            return JsonResponse({"ok": True, "site": {"name": store.store_name, "domain": os.environ.get("DOMAIN", ""), "version": 13, "platform": "deltajanebi"}})

        if action == "dashboard":
            return JsonResponse({"ok": True, "data": {
                "site_name": store.store_name,
                "products": Product.objects.filter(is_active=True).count(),
                "orders": Order.objects.count(),
                "pending_orders": Order.objects.filter(status__in=["payment_pending", "receipt_pending", "payment_rejected"]).count(),
                "users": User.objects.filter(is_staff=False).count(),
            }})

        if action == "categories":
            rows = [{"id": x.id, "name": x.name, "is_active": x.is_active, "parent_id": x.parent_id, "has_image": bool(x.image_url), "product_count": x.products.filter(is_active=True).count()} for x in Category.objects.order_by("order", "name")[:100]]
            return JsonResponse({"ok": True, "data": rows})
        if action == "category_detail":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            return JsonResponse({"ok": True, "data": {"id": item.id, "name": item.name, "is_active": item.is_active, "parent_id": item.parent_id, "has_image": bool(item.image_url), "product_count": item.products.filter(is_active=True).count()}})
        if action == "category_create":
            name = str(payload.get("name") or "").strip()
            if not name: return JsonResponse({"ok": False, "error": "name_required"}, status=400)
            item = Category.objects.create(name=name, slug="", parent_id=payload.get("parent_id") or None)
            return JsonResponse({"ok": True, "data": {"id": item.id, "name": item.name}})
        if action == "category_update":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            if "name" in payload: item.name = str(payload.get("name") or "").strip()[:120]; item.slug = ""
            if "is_active" in payload: item.is_active = bool(payload.get("is_active"))
            if "sort_order" in payload: item.order = max(0, int(payload.get("sort_order") or 0))
            item.save()
            return JsonResponse({"ok": True})
        if action == "category_image_set":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            _category_image_set(item, payload)
            return JsonResponse({"ok": True})
        if action == "category_image_remove":
            item = Category.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("category")
            item.image_url = ""; item.save(update_fields=["image_url"])
            return JsonResponse({"ok": True})

        if action == "products":
            rows = []
            for item in Product.objects.select_related("category").order_by("-created_at")[:100]:
                price = pricing_data(item)
                rows.append({"id": item.id, "name": item.name, "sku": item.public_code or item.sku or "", "price": item.price, "stock": item.stock, "reserved_stock": item.reserved_stock, "is_active": item.is_active, "is_amazing": price["amazing_active"], "category__name": item.category.name if item.category else "", **price})
            return JsonResponse({"ok": True, "data": rows})
        if action == "product_detail":
            item = Product.objects.select_related("category").filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            price = pricing_data(item)
            return JsonResponse({"ok": True, "data": {"id": item.id, "name": item.name, "sku": item.public_code or item.sku or "", "price": item.price, "compare_at_price": item.price if price["promotion_label"] else None, "stock": item.stock, "reserved_stock": item.reserved_stock, "available_stock": item.available_stock, "is_active": item.is_active, "is_amazing": price["amazing_active"], "category_id": item.category_id, "category": item.category.name if item.category else "", "description": item.description, "has_image": bool(item.primary_image), **price}})
        if action == "product_create":
            category = Category.objects.filter(pk=payload.get("category_id"), is_active=True).first() if payload.get("category_id") else None
            name = str(payload.get("name") or "").strip(); price = int(payload.get("price") or 0); stock = int(payload.get("stock") or 0)
            if not name or price <= 0 or stock < 0: return JsonResponse({"ok": False, "error": "invalid_product"}, status=400)
            item = Product.objects.create(category=category, name=name, price=price, stock=stock, description=str(payload.get("description") or ""), source_type=Product.MANUAL)
            return JsonResponse({"ok": True, "data": {"id": item.id, "name": item.name, "sku": item.public_code or ""}})
        if action == "product_update":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            if "price" in payload:
                value = int(payload.get("price") or 0)
                if value <= 0: return JsonResponse({"ok": False, "error": "invalid_base_price"}, status=400)
                item.price = value
                if item.source_type == Product.SYNCED: item.manual_price_override = value
                item.save()
                normalize_prices(item)
            if "discount_price" in payload or "compare_at_price" in payload:
                # New manager uses discount_price. Legacy compare_at_price is ignored unless
                # it is clearly below the base; this avoids reversing Delta's base-price model.
                value = payload.get("discount_price") if "discount_price" in payload else payload.get("compare_at_price")
                if "discount_price" in payload or (value and int(value) < int(item.price)):
                    set_discount_price(item, int(value or 0))
            if "amazing_price" in payload:
                set_amazing_price(item, int(payload.get("amazing_price") or 0))
            if "stock" in payload:
                item.stock = max(0, int(payload.get("stock") or 0))
                if item.source_type == Product.SYNCED: item.manual_stock_override = item.stock
                item.save()
            if "is_active" in payload:
                item.is_active = bool(payload.get("is_active")); item.save(update_fields=["is_active", "updated_at"])
            if "name" in payload:
                item.name = str(payload.get("name") or "").strip()[:300]; item.manual_name_override = item.name if item.source_type == Product.SYNCED else item.manual_name_override; item.slug = ""; item.save()
            if "description" in payload:
                item.description = str(payload.get("description") or ""); item.save(update_fields=["description", "updated_at"])
            return JsonResponse({"ok": True, "data": pricing_data(item)})
        if action == "product_image_set":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            name, content = _decode(payload)
            if item.image: item.image.delete(save=False)
            item.image.save(name, content, save=True)
            return JsonResponse({"ok": True})
        if action == "product_image":
            item = Product.objects.filter(pk=payload.get("id")).first()
            if not item: return _not_found("product")
            if not item.image: return JsonResponse({"ok": False, "error": "image_not_found"}, status=404)
            return JsonResponse({"ok": True, "data": {"image_b64": _field_b64(item.image), "filename": os.path.basename(item.image.name), "name": item.name, "sku": item.public_code or item.sku or ""}})

        if action == "orders":
            rows = []
            for item in Order.objects.select_related("user").order_by("-created_at")[:100]:
                rows.append({"id": item.id, "code": str(item.id), "full_name": item.full_name, "mobile": item.phone, "total": item.total, "status": _api_status(item), "status_label": item.get_status_display(), "tracking_code": item.tracking_code, "reservation_remaining_seconds": item.reservation_remaining_seconds, "created_at": item.created_at.isoformat()})
            return JsonResponse({"ok": True, "data": rows})
        if action == "order_detail":
            item = Order.objects.select_related("user").prefetch_related("items__product").filter(pk=payload.get("id")).first()
            if not item: return _not_found("order")
            return JsonResponse({"ok": True, "data": _order_data(item)})
        if action == "order_update":
            item = Order.objects.select_related("user").filter(pk=payload.get("id")).first()
            if not item: return _not_found("order")
            incoming = str(payload.get("status") or "")
            native = API_TO_DELTA_STATUS.get(incoming)
            if not native: return JsonResponse({"ok": False, "error": "invalid_status"}, status=400)
            if incoming == "paid" and item.payment_status != Order.PAY_PAID:
                mark_paid(item)
            else:
                item.status = native
                if "tracking_code" in payload: item.tracking_code = str(payload.get("tracking_code") or "").strip()[:100]
                item.save()
            return JsonResponse({"ok": True})

        if action == "receipts":
            rows = []
            for item in Order.objects.exclude(receipt="").order_by("-created_at")[:100]:
                status = "approved" if item.payment_status == Order.PAY_PAID else ("rejected" if item.payment_status == Order.PAY_REJECTED else "pending")
                rows.append({"id": item.id, "order_id": item.id, "order_code": str(item.id), "total": item.total, "status": status, "full_name": item.full_name, "created_at": item.created_at.isoformat()})
            return JsonResponse({"ok": True, "data": rows})
        if action == "receipt_detail":
            item = Order.objects.filter(pk=payload.get("id")).first()
            if not item or not item.receipt: return _not_found("receipt")
            status = "approved" if item.payment_status == Order.PAY_PAID else ("rejected" if item.payment_status == Order.PAY_REJECTED else "pending")
            return JsonResponse({"ok": True, "data": {"id": item.id, "order_id": item.id, "order_code": str(item.id), "total": item.total, "status": status, "full_name": item.full_name, "rejection_reason": item.receipt_rejection_reason}})
        if action == "receipt_image":
            item = Order.objects.filter(pk=payload.get("id")).first()
            if not item or not item.receipt: return _not_found("receipt")
            return JsonResponse({"ok": True, "data": {"image_b64": _field_b64(item.receipt), "filename": os.path.basename(item.receipt.name) or "receipt.jpg"}})
        if action in {"receipt_update", "receipt_set"}:
            item = Order.objects.select_related("user").filter(pk=payload.get("id")).first()
            if not item or not item.receipt: return _not_found("receipt")
            status = str(payload.get("status") or "")
            if status == "approved":
                if item.payment_status != Order.PAY_PAID: mark_paid(item)
                item.receipt_rejection_reason = ""; item.save(update_fields=["receipt_rejection_reason", "updated_at"])
                try: email_customer(item, f"پرداخت سفارش #{item.id} تایید شد", "رسید پرداخت شما تایید شد و سفارش وارد مرحله آماده‌سازی شد.")
                except Exception: pass
            elif status == "rejected":
                reason = str(payload.get("reason") or "رسید پرداخت توسط مدیر رد شد.").strip()[:500]
                item.payment_status = Order.PAY_REJECTED; item.status = "payment_rejected"; item.receipt_rejection_reason = reason; item.save()
                try: release_order_stock(item)
                except Exception: pass
                try: email_customer(item, f"رسید سفارش #{item.id} رد شد", f"رسید پرداخت تأیید نشد. دلیل: {reason}")
                except Exception: pass
            else:
                return JsonResponse({"ok": False, "error": "invalid_receipt_status"}, status=400)
            return JsonResponse({"ok": True})

        if action in {"users", "user_search"}:
            query = str(payload.get("query") or "").strip()
            users = User.objects.filter(is_staff=False).order_by("-date_joined")
            if query:
                users = users.filter(Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query) | Q(customer_code__iexact=query) | Q(phone__icontains=query)).distinct()
            return JsonResponse({"ok": True, "data": [_user_row(x) for x in users[:50]], "query": query})
        if action == "user_detail":
            item = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not item: return _not_found("user")
            return JsonResponse({"ok": True, "data": _user_detail(item)})
        if action == "user_update":
            item = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not item: return _not_found("user")
            if "email" in payload:
                email = str(payload.get("email") or "").strip().lower()
                try: validate_email(email)
                except ValidationError: return JsonResponse({"ok": False, "error": "invalid_email"}, status=400)
                if User.objects.filter(email__iexact=email).exclude(pk=item.pk).exists(): return JsonResponse({"ok": False, "error": "email_exists"}, status=409)
                item.email = email
            if "phone" in payload:
                phone = normalize_mobile(payload.get("phone"))
                if not phone.startswith("09") or len(phone) != 11 or not phone.isdigit(): return JsonResponse({"ok": False, "error": "invalid_phone"}, status=400)
                if User.objects.filter(phone=phone).exclude(pk=item.pk).exists(): return JsonResponse({"ok": False, "error": "phone_exists"}, status=409)
                item.phone = phone
            if "first_name" in payload: item.first_name = str(payload.get("first_name") or "").strip()[:150]
            if "last_name" in payload: item.last_name = str(payload.get("last_name") or "").strip()[:150]
            if "is_active" in payload: item.is_active = bool(payload.get("is_active"))
            item.save()
            return JsonResponse({"ok": True, "data": _user_row(item)})
        if action == "user_password_reset":
            item = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not item: return _not_found("user")
            send_password_reset_email(request, item)
            return JsonResponse({"ok": True, "data": {"email": item.email}})
        if action == "broadcast_email":
            subject = str(payload.get("subject") or "").strip(); body = str(payload.get("body") or "").strip()
            if len(subject) < 2 or len(subject) > 180 or len(body) < 2 or len(body) > 20000: return JsonResponse({"ok": False, "error": "invalid_broadcast"}, status=400)
            recipients = list(User.objects.filter(is_staff=False, is_active=True).exclude(email="").values_list("email", flat=True))
            sent = send_broadcast_email(subject, body, recipients)
            return JsonResponse({"ok": True, "data": {"recipients": len(recipients), "sent": sent}})

        if action == "settings_get": return JsonResponse({"ok": True, "data": _settings_data()})
        if action == "settings_update":
            mapping = {"site_name": "store_name", "announcement": "top_bar_text", "shipping_fee": "shipping_cost", "free_shipping_threshold": "free_shipping_threshold", "card_number": "card_number", "card_owner": "card_owner", "address": "address", "phone": "footer_phone", "contact_phone": "phone", "contact_email": "contact_email", "footer_description": "footer_description", "support_text": "support_text", "terms_text": "terms_text"}
            for key, field in mapping.items():
                if key in payload:
                    value = payload.get(key)
                    if field in {"shipping_cost", "free_shipping_threshold"}: value = max(0, int(value or 0))
                    else: value = str(value or "").strip()
                    setattr(store, field, value)
            store.save()
            return JsonResponse({"ok": True, "data": _settings_data()})
        if action == "logo_set":
            name, content = _decode(payload); 
            if store.logo: store.logo.delete(save=False)
            store.logo.save(name, content, save=True); return JsonResponse({"ok": True})
        if action == "logo_remove":
            if store.logo: store.logo.delete(save=False)
            store.logo = ""; store.logo_url = ""; store.save(update_fields=["logo", "logo_url"]); return JsonResponse({"ok": True})

        if action == "banners": return JsonResponse({"ok": True, "data": [{"id": x.id, "title": x.title, "subtitle": "", "link": x.target_url, "is_active": x.is_active, "sort_order": x.order, "has_desktop_image": bool(x.image_src), "has_mobile_image": bool(x.mobile_image_src)} for x in Banner.objects.all()[:100]]})
        if action == "banner_detail":
            x = Banner.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("banner")
            return JsonResponse({"ok": True, "data": {"id": x.id, "title": x.title, "subtitle": "", "link": x.target_url, "is_active": x.is_active, "sort_order": x.order, "has_desktop_image": bool(x.image_src), "has_mobile_image": bool(x.mobile_image_src)}})
        if action == "banner_create":
            dn, desktop = _decode(payload, "desktop_image_b64"); mn, mobile = _decode(payload, "mobile_image_b64")
            x = Banner(title=str(payload.get("title") or "")[:160], target_url=str(payload.get("link") or "")[:4096])
            x.image.save(dn, desktop, save=False); x.mobile_image.save(mn, mobile, save=False); x.save()
            return JsonResponse({"ok": True, "data": {"id": x.id}})
        if action == "banner_update":
            x = Banner.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("banner")
            if "title" in payload: x.title = str(payload.get("title") or "")[:160]
            if "link" in payload: x.target_url = str(payload.get("link") or "")[:4096]
            if "is_active" in payload: x.is_active = bool(payload.get("is_active"))
            if "sort_order" in payload: x.order = max(0, int(payload.get("sort_order") or 0))
            x.save(); return JsonResponse({"ok": True})
        if action == "banner_delete":
            deleted, _ = Banner.objects.filter(pk=payload.get("id")).delete(); return JsonResponse({"ok": bool(deleted)})

        if action == "socials": return JsonResponse({"ok": True, "data": [{"id": x.id, "platform": x.platform, "platform_label": x.get_platform_display(), "title": x.label, "url": x.url, "is_active": x.is_active, "sort_order": x.order} for x in SocialLink.objects.all()[:100]]})
        if action == "social_detail":
            x = SocialLink.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("social")
            return JsonResponse({"ok": True, "data": {"id": x.id, "platform": x.platform, "platform_label": x.get_platform_display(), "title": x.label, "url": x.url, "is_active": x.is_active, "sort_order": x.order}})
        if action == "social_create":
            platform = str(payload.get("platform") or "other"); platform = platform if platform in dict(SocialLink.PLATFORM_CHOICES) else "other"
            x = SocialLink.objects.create(platform=platform, label=str(payload.get("title") or dict(SocialLink.PLATFORM_CHOICES).get(platform) or "شبکه اجتماعی")[:80], url=str(payload.get("url") or ""))
            return JsonResponse({"ok": True, "data": {"id": x.id}})
        if action == "social_update":
            x = SocialLink.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("social")
            if "platform" in payload and payload.get("platform") in dict(SocialLink.PLATFORM_CHOICES): x.platform = payload.get("platform")
            if "title" in payload: x.label = str(payload.get("title") or "")[:80]
            if "url" in payload: x.url = str(payload.get("url") or "")
            if "is_active" in payload: x.is_active = bool(payload.get("is_active"))
            if "sort_order" in payload: x.order = max(0, int(payload.get("sort_order") or 0))
            x.save(); return JsonResponse({"ok": True})
        if action == "social_delete":
            deleted, _ = SocialLink.objects.filter(pk=payload.get("id")).delete(); return JsonResponse({"ok": bool(deleted)})

        if action == "enamad_set":
            x, _ = TrustBadge.objects.get_or_create(badge_type=TrustBadge.ENAMAD); name, content = _decode(payload)
            if x.image: x.image.delete(save=False)
            x.image.save(name, content, save=False); x.image_url = ""; x.target_url = str(payload.get("target_url") or x.target_url or "")[:4096]; x.is_active = True; x.save(); return JsonResponse({"ok": True})
        if action == "enamad_remove":
            x = TrustBadge.objects.filter(badge_type=TrustBadge.ENAMAD).first()
            if x:
                if x.image: x.image.delete(save=False)
                x.image = ""; x.image_url = ""; x.save(update_fields=["image", "image_url"])
            return JsonResponse({"ok": True})

        if action == "stories": return JsonResponse({"ok": True, "data": [_story_data(x) for x in ProductStory.objects.all()[:100]]})
        if action == "story_detail":
            x = ProductStory.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("story")
            return JsonResponse({"ok": True, "data": _story_data(x)})
        if action == "story_create":
            title = str(payload.get("title") or "").strip()[:160]; target = str(payload.get("target_url") or "").strip()[:500]; hours = int(payload.get("duration_hours") or 0); media_type = str(payload.get("media_type") or "image")
            if not title or not target or hours <= 0 or media_type not in {"image", "video"}: return JsonResponse({"ok": False, "error": "invalid_story"}, status=400)
            name, content = _decode(payload, "media_b64", MAX_STORY_BYTES, "story.mp4" if media_type == "video" else "story.jpg")
            x = ProductStory(title=title, target_url=target, media_type=media_type, expires_at=timezone.now() + timedelta(hours=min(hours, 720)), is_active=True); x.media.save(name, content, save=False); x.save()
            return JsonResponse({"ok": True, "data": _story_data(x)})
        if action == "story_update":
            x = ProductStory.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("story")
            if "title" in payload: x.title = str(payload.get("title") or "")[:160]
            if "target_url" in payload: x.target_url = str(payload.get("target_url") or "")[:500]
            if "is_active" in payload: x.is_active = bool(payload.get("is_active"))
            if "duration_hours" in payload: x.expires_at = timezone.now() + timedelta(hours=max(1, min(int(payload.get("duration_hours") or 1), 720)))
            x.save(); return JsonResponse({"ok": True, "data": _story_data(x)})
        if action == "story_media_set":
            x = ProductStory.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("story")
            media_type = str(payload.get("media_type") or "image")
            if media_type not in {"image", "video"}: return JsonResponse({"ok": False, "error": "invalid_media_type"}, status=400)
            name, content = _decode(payload, "media_b64", MAX_STORY_BYTES, "story.mp4" if media_type == "video" else "story.jpg")
            if x.media: x.media.delete(save=False)
            x.media_type = media_type; x.media.save(name, content, save=False); x.save(); return JsonResponse({"ok": True, "data": _story_data(x)})
        if action == "story_delete":
            x = ProductStory.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("story")
            if x.media: x.media.delete(save=False)
            x.delete(); return JsonResponse({"ok": True})

        if action == "discounts":
            return JsonResponse({"ok": True, "data": [{"id": x.id, "code": x.code, "discount_type": x.discount_type, "percent": x.value if x.discount_type == DiscountCode.PERCENT else 0, "amount": x.value if x.discount_type == DiscountCode.FIXED else 0, "is_active": x.is_active} for x in DiscountCode.objects.all()[:100]]})
        if action == "discount_create":
            code = str(payload.get("code") or "").strip().upper(); dtype = str(payload.get("discount_type") or "percent"); value = int(payload.get("value") or payload.get("percent") or payload.get("amount") or 0)
            if not code or dtype not in {DiscountCode.PERCENT, DiscountCode.FIXED} or value <= 0: return JsonResponse({"ok": False, "error": "invalid_discount"}, status=400)
            x = DiscountCode.objects.create(code=code, discount_type=dtype, value=value); return JsonResponse({"ok": True, "data": {"id": x.id}})
        if action == "discount_update":
            x = DiscountCode.objects.filter(pk=payload.get("id")).first()
            if not x: return _not_found("discount")
            if "is_active" in payload: x.is_active = bool(payload.get("is_active"))
            x.save(); return JsonResponse({"ok": True})
        if action == "discount_delete":
            deleted, _ = DiscountCode.objects.filter(pk=payload.get("id")).delete(); return JsonResponse({"ok": bool(deleted)})

        if action == "backup_status": return JsonResponse({"ok": True, "data": _backup_status()})
        if action == "backup_interval_set":
            minutes = int(payload.get("minutes") or 0)
            if minutes != 0 and minutes < 5: return JsonResponse({"ok": False, "error": "minimum_interval_is_5"}, status=400)
            if minutes > 43200: return JsonResponse({"ok": False, "error": "interval_too_large"}, status=400)
            addon = AddonSetting.load(); addon.backup_interval_minutes = minutes; addon.save(update_fields=["backup_interval_minutes", "updated_at"]); return JsonResponse({"ok": True, "data": _backup_status()})
        if action == "backup_create":
            path = create_backup_archive(str(payload.get("label") or "manual")[:24])
            try:
                size = path.stat().st_size
                if size > MAX_BOT_BACKUP_BYTES: return JsonResponse({"ok": False, "error": "backup_too_large", "data": {"filename": path.name, "size": size, "too_large": True}}, status=413)
                raw = path.read_bytes(); addon = AddonSetting.load(); addon.last_backup_at = timezone.now(); addon.save(update_fields=["last_backup_at", "updated_at"])
                return JsonResponse({"ok": True, "data": {"filename": path.name, "size": size, "too_large": False, "backup_b64": base64.b64encode(raw).decode("ascii")}})
            finally: path.unlink(missing_ok=True)
        if action == "backup_touch":
            addon = AddonSetting.load(); addon.last_backup_at = timezone.now(); addon.save(update_fields=["last_backup_at", "updated_at"]); return JsonResponse({"ok": True, "data": _backup_status()})
        if action == "backup_restore":
            filename = str(payload.get("filename") or "restore.deltabackup")
            if not filename.lower().endswith(".deltabackup"): return JsonResponse({"ok": False, "error": "invalid_backup_filename"}, status=400)
            raw = base64.b64decode(str(payload.get("backup_b64") or ""), validate=True)
            if not raw or len(raw) > MAX_BOT_BACKUP_BYTES: return JsonResponse({"ok": False, "error": "invalid_backup_size"}, status=413)
            with tempfile.NamedTemporaryFile(suffix=".deltabackup", delete=False) as handle: handle.write(raw); temp_path = Path(handle.name)
            try:
                manifest = validate_backup_archive(temp_path); emergency = restore_backup_archive(temp_path); emergency_name = emergency.name if emergency else None
                if emergency: emergency.unlink(missing_ok=True)
                return JsonResponse({"ok": True, "data": {"created_at": manifest.get("created_at"), "emergency_backup": emergency_name}})
            finally: temp_path.unlink(missing_ok=True)

        if action == "events_poll":
            limit = max(1, min(int(payload.get("limit") or 20), 100))
            rows = [{"id": x.id, "kind": x.kind, "payload": x.payload, "created_at": x.created_at.isoformat()} for x in BotEvent.objects.filter(acknowledged_at__isnull=True).order_by("id")[:limit]]
            return JsonResponse({"ok": True, "data": rows})
        if action == "events_ack":
            ids = [int(x) for x in (payload.get("ids") or []) if str(x).isdigit()]
            BotEvent.objects.filter(id__in=ids, acknowledged_at__isnull=True).update(acknowledged_at=timezone.now())
            return JsonResponse({"ok": True})

        return JsonResponse({"ok": False, "error": "unknown_action", "action": action}, status=400)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        # A single API operation must never alter/revoke the site's API key or connection.
        return JsonResponse({"ok": False, "error": "operation_failed", "detail": str(exc)[:500]}, status=500)
