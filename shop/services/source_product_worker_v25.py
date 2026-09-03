"""Single-product sync body used by the hard-isolated v25 catalog runner.

The caller executes this code in a separate OS process. Any C-extension, DNS,
Pillow/lxml parser or socket stall can therefore be killed by the parent without
freezing the full catalog job. Each database mutation is transaction-scoped so a
killed child cannot leave a half-written product/offer update behind.
"""
from django.db import transaction
from django.utils import timezone

from shop.source_offer_models import ProductSourceOffer
from shop.services import source_catalog_v22 as catalog
from shop.services import source_identity_v22 as identity
from shop.services.source_sync import SourceNotProductError


def _result(**extra):
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


def _mark_source_missing(site, url):
    offer = (
        ProductSourceOffer.objects.filter(source_site=site, source_url=url)
        .select_related("product")
        .first()
    )
    if not offer:
        return 0
    product = offer.product
    offer.stock = 0
    offer.is_active = False
    offer.last_seen_at = timezone.now()
    offer.save(update_fields=["stock", "is_active", "last_seen_at", "updated_at"])
    identity.aggregate_product(product)
    return int(product.pk or 0)


def sync_one(site, url):
    """Synchronize exactly one source URL and return a JSON-safe summary."""
    try:
        with transaction.atomic():
            product, created, changes = catalog.upsert_source_product_with_changes(site, url)
        return _result(
            created=1 if created else 0,
            changed=1 if (created or changes) else 0,
            product_id=int(product.pk or 0),
            changes=changes or {},
        )
    except catalog.CatalogSkip as exc:
        try:
            with transaction.atomic():
                product, created = catalog.import_unpriced_catalog_product(site, url)
            return _result(
                created=1 if created else 0,
                changed=1 if created else 0,
                product_id=int(product.pk or 0),
                warning=f"{site.name}: بدون قیمت وارد/به‌روزرسانی شد: {exc}"[:700],
            )
        except SourceNotProductError as nested:
            with transaction.atomic():
                product_id = _mark_source_missing(site, url)
            return _result(
                skipped=1,
                product_id=product_id,
                warning=f"{site.name}: {nested}"[:700],
            )
        except Exception as nested:
            return _result(
                errors=1,
                warning=f"{site.name}: import-unpriced: {nested}"[:700],
            )
    except SourceNotProductError as exc:
        with transaction.atomic():
            product_id = _mark_source_missing(site, url)
        return _result(
            skipped=1,
            product_id=product_id,
            warning=f"{site.name}: {exc}"[:700],
        )
    except Exception as exc:
        return _result(
            errors=1,
            warning=f"{site.name}: {exc}"[:700],
        )
