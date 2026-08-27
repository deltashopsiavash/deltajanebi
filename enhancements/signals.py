from django.db.models.signals import post_save
from django.dispatch import receiver

from shop.models import Order

from .models import BotEvent


def _payload(order):
    return {
        "order_id": order.id,
        "code": str(order.id),
        "full_name": order.full_name,
        "mobile": order.phone,
        "province": order.province,
        "city": order.city,
        "address": order.address,
        "subtotal": order.subtotal,
        "discount_amount": order.discount_amount,
        "shipping": order.shipping_cost + order.packaging_cost,
        "total": order.total,
        "status": order.status,
        "status_label": order.get_status_display(),
        "payment_method": order.payment_method,
        "payment_method_label": order.get_payment_method_display(),
        "tracking_code": order.tracking_code,
        "reservation_remaining_seconds": order.reservation_remaining_seconds,
    }


@receiver(post_save, sender=Order, dispatch_uid="delta_external_bot_events")
def queue_order_events(sender, instance, created, update_fields=None, **kwargs):
    fields = set(update_fields or [])
    if created:
        BotEvent.objects.create(kind="order_created", payload=_payload(instance))
        return
    if instance.receipt and "receipt" in fields:
        payload = _payload(instance)
        payload["receipt_id"] = instance.id
        BotEvent.objects.create(kind="receipt_uploaded", payload=payload)
    if fields.intersection({"status", "payment_status", "tracking_code"}):
        BotEvent.objects.create(kind="order_status", payload=_payload(instance))
