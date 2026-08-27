import shutil
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from shop.models import Announcement, Category, Product, SourceSite, User
from shop.services.source_catalog import CatalogSkip, discover_product_urls, source_products, upsert_source_product_with_changes
from shop.services.source_sync import SourceNotProductError
from shop.services.wallet import adjust_wallet, wallet_balance, wallet_history
from shop.source_registry import normalize_site_url

from .pricing import pricing_data
from .site_api import _authorized, _json, _not_found, _unauthorized, bot_api as v13_bot_api


def _source_row(site):
    products = Product.objects.filter(source_type=Product.SYNCED, source_url__icontains=site.hostname)
    return {
        "id": site.id,
        "name": site.name,
        "base_url": site.base_url,
        "hostname": site.hostname,
        "brand_terms": site.brand_terms,
        "is_active": site.is_active,
        "bulk_import_enabled": site.bulk_import_enabled,
        "default_markup_type": site.default_markup_type,
        "default_markup_value": str(site.default_markup_value),
        "markup_label": site.markup_label(),
        "last_bulk_sync_at": site.last_bulk_sync_at.isoformat() if site.last_bulk_sync_at else None,
        "last_discovered_count": site.last_discovered_count,
        "product_count": products.count(),
        "available_count": products.filter(stock__gt=0).count(),
    }


def _product_row(item):
    data = pricing_data(item)
    return {
        "id": item.id,
        "name": item.name,
        "sku": item.public_code or item.sku or "",
        "source_type": item.source_type,
        "source_url": item.source_url,
        "source_price": item.source_price,
        "price": item.price,
        "stock": item.stock,
        "reserved_stock": item.reserved_stock,
        "available_stock": item.available_stock,
        "is_active": item.is_active,
        "category": item.category.name if item.category_id else "",
        "last_synced_at": item.last_synced_at.isoformat() if item.last_synced_at else None,
        "sync_error": item.sync_error or "",
        **data,
    }


def _catalog_urls(site):
    if site.bulk_import_enabled:
        urls = discover_product_urls(site)
        site.last_discovered_count = len(urls)
        site.save(update_fields=["last_discovered_count"])
        if urls:
            return list(dict.fromkeys(urls))
    return list(dict.fromkeys(source_products(site).exclude(source_url="").values_list("source_url", flat=True)))


def _sync_all_sources():
    sites = list(SourceSite.objects.filter(is_active=True).order_by("id"))
    totals = {"sites": len(sites), "checked": 0, "created": 0, "changed": 0, "skipped": 0, "errors": 0}
    samples = []
    for site in sites:
        try:
            urls = _catalog_urls(site)
        except Exception as exc:
            urls = list(source_products(site).exclude(source_url="").values_list("source_url", flat=True))
            samples.append(f"{site.name}: discovery: {str(exc)[:140]}")
        for url in urls:
            totals["checked"] += 1
            try:
                _, created, changes = upsert_source_product_with_changes(site, url)
                if created:
                    totals["created"] += 1
                if created or changes:
                    totals["changed"] += 1
            except (SourceNotProductError, CatalogSkip):
                totals["skipped"] += 1
            except Exception as exc:
                totals["errors"] += 1
                if len(samples) < 12:
                    samples.append(f"{site.name}: {str(exc)[:160]}")
        if site.bulk_import_enabled:
            site.last_bulk_sync_at = timezone.now()
            site.save(update_fields=["last_bulk_sync_at"])
    totals["warnings"] = samples
    return totals


def _purge_catalog():
    count = Product.objects.count()
    for item in Product.objects.exclude(image="").iterator(chunk_size=200):
        try:
            item.image.delete(save=False)
        except Exception:
            pass
    Product.objects.all().delete()
    Category.objects.filter(image_url__startswith="/media/products/").update(image_url="")
    products_dir = Path(settings.MEDIA_ROOT) / "products"
    if products_dir.exists():
        shutil.rmtree(products_dir, ignore_errors=True)
    return count


def _announcement_row(item):
    return {
        "id": item.id,
        "text": item.text,
        "is_active": item.is_active,
        "created_at": item.created_at.isoformat() if item.created_at else None,
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

    try:
        if action == "delta_products":
            mode = str(payload.get("mode") or "all").strip().lower()
            query = str(payload.get("query") or "").strip()
            rows = Product.objects.select_related("category").order_by("-created_at")
            if mode == "manual":
                rows = rows.filter(source_type=Product.MANUAL)
            elif mode == "synced":
                rows = rows.filter(source_type=Product.SYNCED)
            elif mode == "offers":
                now = timezone.now()
                rows = rows.filter(sale_price__isnull=False).filter(Q(sale_starts_at__isnull=True) | Q(sale_starts_at__lte=now)).filter(Q(sale_ends_at__isnull=True) | Q(sale_ends_at__gt=now))
            if query:
                rows = rows.filter(Q(name__icontains=query) | Q(public_code__icontains=query) | Q(sku__icontains=query) | Q(source_product_code__icontains=query))
            return JsonResponse({"ok": True, "data": [_product_row(x) for x in rows[:100]], "mode": mode, "query": query})

        if action == "source_sites":
            return JsonResponse({"ok": True, "data": [_source_row(x) for x in SourceSite.objects.order_by("id")[:100]]})

        if action == "source_site_detail":
            site = SourceSite.objects.filter(pk=payload.get("id")).first()
            if not site:
                return _not_found("source_site")
            return JsonResponse({"ok": True, "data": _source_row(site)})

        if action == "source_site_create":
            name = str(payload.get("name") or "").strip()[:120]
            base_url, hostname = normalize_site_url(payload.get("base_url"))
            if not name:
                return JsonResponse({"ok": False, "error": "name_required"}, status=400)
            if SourceSite.objects.filter(hostname=hostname).exists():
                return JsonResponse({"ok": False, "error": "source_site_exists"}, status=409)
            markup_type = str(payload.get("default_markup_type") or SourceSite.MARKUP_PERCENT)
            if markup_type not in dict(SourceSite.MARKUP_CHOICES):
                return JsonResponse({"ok": False, "error": "invalid_markup_type"}, status=400)
            site = SourceSite.objects.create(
                name=name,
                base_url=base_url,
                hostname=hostname,
                brand_terms=str(payload.get("brand_terms") or "")[:500],
                is_active=bool(payload.get("is_active", True)),
                bulk_import_enabled=bool(payload.get("bulk_import_enabled", False)),
                default_markup_type=markup_type,
                default_markup_value=payload.get("default_markup_value") or 0,
            )
            return JsonResponse({"ok": True, "data": _source_row(site)})

        if action == "source_site_update":
            site = SourceSite.objects.filter(pk=payload.get("id")).first()
            if not site:
                return _not_found("source_site")
            if "name" in payload:
                site.name = str(payload.get("name") or "").strip()[:120]
            if "base_url" in payload:
                base_url, hostname = normalize_site_url(payload.get("base_url"))
                if SourceSite.objects.exclude(pk=site.pk).filter(hostname=hostname).exists():
                    return JsonResponse({"ok": False, "error": "source_site_exists"}, status=409)
                site.base_url, site.hostname = base_url, hostname
            if "brand_terms" in payload:
                site.brand_terms = str(payload.get("brand_terms") or "")[:500]
            if "is_active" in payload:
                site.is_active = bool(payload.get("is_active"))
            if "bulk_import_enabled" in payload:
                site.bulk_import_enabled = bool(payload.get("bulk_import_enabled"))
            if "default_markup_type" in payload:
                mt = str(payload.get("default_markup_type") or "")
                if mt not in dict(SourceSite.MARKUP_CHOICES):
                    return JsonResponse({"ok": False, "error": "invalid_markup_type"}, status=400)
                site.default_markup_type = mt
            if "default_markup_value" in payload:
                site.default_markup_value = payload.get("default_markup_value") or 0
            site.save()
            return JsonResponse({"ok": True, "data": _source_row(site)})

        if action == "source_site_delete":
            site = SourceSite.objects.filter(pk=payload.get("id")).first()
            if not site:
                return _not_found("source_site")
            info = _source_row(site)
            site.delete()
            return JsonResponse({"ok": True, "data": info})

        if action == "source_sync_all":
            return JsonResponse({"ok": True, "data": _sync_all_sources()})

        if action == "catalog_purge":
            if str(payload.get("confirm") or "") != "PURGE_ALL_PRODUCTS":
                return JsonResponse({"ok": False, "error": "confirmation_required", "data": {"product_count": Product.objects.count()}}, status=409)
            return JsonResponse({"ok": True, "data": {"deleted": _purge_catalog()}})

        if action == "announcements":
            return JsonResponse({"ok": True, "data": [_announcement_row(x) for x in Announcement.objects.order_by("-created_at")[:100]]})
        if action == "announcement_create":
            text = str(payload.get("text") or "").strip()
            if not text:
                return JsonResponse({"ok": False, "error": "text_required"}, status=400)
            item = Announcement.objects.create(text=text, is_active=bool(payload.get("is_active", True)))
            return JsonResponse({"ok": True, "data": _announcement_row(item)})
        if action == "announcement_update":
            item = Announcement.objects.filter(pk=payload.get("id")).first()
            if not item:
                return _not_found("announcement")
            if "text" in payload:
                item.text = str(payload.get("text") or "").strip()
            if "is_active" in payload:
                item.is_active = bool(payload.get("is_active"))
            item.save()
            return JsonResponse({"ok": True, "data": _announcement_row(item)})
        if action == "announcement_delete":
            deleted, _ = Announcement.objects.filter(pk=payload.get("id")).delete()
            if not deleted:
                return _not_found("announcement")
            return JsonResponse({"ok": True})

        if action == "wallet_history":
            user = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not user:
                return _not_found("user")
            limit = max(1, min(int(payload.get("limit") or 20), 100))
            return JsonResponse({"ok": True, "data": {"user_id": user.id, "balance": wallet_balance(user.id), "transactions": wallet_history(user.id, limit)}})
        if action == "wallet_adjust":
            user = User.objects.filter(pk=payload.get("id"), is_staff=False).first()
            if not user:
                return _not_found("user")
            amount = int(payload.get("amount") or 0)
            reason = str(payload.get("reason") or "")[:1000]
            admin_id = str(payload.get("admin_id") or "external-bot")[:64]
            balance = adjust_wallet(user.id, amount, reason, admin_id)
            return JsonResponse({"ok": True, "data": {"user_id": user.id, "balance": balance}})

    except (TypeError, ValueError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": "delta_action_failed", "detail": str(exc)[:700]}, status=500)

    return v13_bot_api(request)
