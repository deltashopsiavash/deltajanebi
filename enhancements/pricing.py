from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from .models import ProductAmazing


def amazing_record(product, create=False):
    try:
        return product.amazing_offer
    except ObjectDoesNotExist:
        if create:
            return ProductAmazing.objects.create(product=product)
        return None


def is_amazing_active(product):
    offer = amazing_record(product)
    if not offer or not offer.is_active or not offer.price:
        return False
    if int(offer.price) >= int(product.price or 0):
        return False
    return not offer.expires_at or offer.expires_at > timezone.now()


def amazing_price_for(product):
    offer = amazing_record(product)
    return int(offer.price) if offer and offer.price else None


def effective_price(product):
    offer = amazing_record(product)
    if offer and offer.is_active and offer.price and int(offer.price) < int(product.price or 0):
        if not offer.expires_at or offer.expires_at > timezone.now():
            return int(offer.price)
    # Delta's existing timed sale remains the normal discount and keeps all old behavior.
    if product.is_sale_active:
        return int(product.sale_price)
    return int(product.price or 0)


def promotion_label(product):
    if is_amazing_active(product):
        return "شگفت‌انگیز"
    if product.is_sale_active:
        return "تخفیف"
    return ""


def pricing_data(product):
    offer = amazing_record(product)
    amazing_active = is_amazing_active(product)
    discount_active = bool(product.is_sale_active)
    return {
        "base_price": int(product.price or 0),
        "discount_price": int(product.sale_price) if product.sale_price else None,
        "discount_active": discount_active,
        "discount_starts_at": product.sale_starts_at.isoformat() if product.sale_starts_at else None,
        "discount_ends_at": product.sale_ends_at.isoformat() if product.sale_ends_at else None,
        "amazing_price": int(offer.price) if offer and offer.price else None,
        "amazing_active": amazing_active,
        "amazing_until": offer.expires_at.isoformat() if offer and offer.expires_at else None,
        "effective_price": effective_price(product),
        "promotion_label": "شگفت‌انگیز" if amazing_active else ("تخفیف" if discount_active else ""),
    }


def set_discount_price(product, value):
    value = int(value or 0)
    if value == 0:
        product.clear_sale()
        return
    if value >= int(product.price or 0):
        raise ValueError("قیمت تخفیف باید از قیمت اصلی کمتر باشد.")
    product.sale_price = value
    # A price entered by the manager is immediately active. Existing timed-sale
    # controls are not removed; they can still add a schedule afterwards.
    product.sale_starts_at = None
    product.sale_ends_at = None
    product.save(update_fields=["sale_price", "sale_starts_at", "sale_ends_at", "updated_at"])


def set_amazing_price(product, value, expires_at=None):
    offer = amazing_record(product, create=True)
    value = int(value or 0)
    if value == 0:
        offer.price = None
        offer.is_active = False
        offer.expires_at = None
    else:
        if value >= int(product.price or 0):
            raise ValueError("قیمت شگفت‌انگیز باید از قیمت اصلی کمتر باشد.")
        offer.price = value
        offer.is_active = True
        offer.expires_at = expires_at
    offer.save()
    return offer


def normalize_prices(product):
    changed = []
    if product.sale_price and int(product.sale_price) >= int(product.price or 0):
        product.sale_price = None
        product.sale_starts_at = None
        product.sale_ends_at = None
        changed += ["sale_price", "sale_starts_at", "sale_ends_at"]
    if changed:
        product.save(update_fields=[*changed, "updated_at"])
    offer = amazing_record(product)
    if offer and offer.price and int(offer.price) >= int(product.price or 0):
        offer.price = None
        offer.is_active = False
        offer.expires_at = None
        offer.save()
