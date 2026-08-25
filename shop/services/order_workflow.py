from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from shop.models import Order, Product

RESERVATION_MINUTES = 45
RESERVATION_EXPIRED_MARKER = "[reservation_expired]"


def reservation_deadline():
    return timezone.now() + timedelta(minutes=RESERVATION_MINUTES)


def release_order_stock(order):
    """Release a temporary reservation without changing physical stock."""
    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related("items__product").get(pk=order.pk)
        if locked.stock_committed or locked.reservation_released:
            return False
        for item in locked.items.all():
            if item.product_id:
                product = Product.objects.select_for_update().filter(pk=item.product_id).first()
                if product:
                    product.reserved_stock = max(0, product.reserved_stock - item.quantity)
                    product.save(update_fields=["reserved_stock"])
        locked.reservation_released = True
        locked.save(update_fields=["reservation_released", "updated_at"])
        order.reservation_released = True
        return True


def commit_order_stock(order):
    """Convert an active reservation to a real stock deduction exactly once."""
    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related("items__product").get(pk=order.pk)
        if locked.stock_committed:
            order.stock_committed = True
            order.reservation_released = True
            return False
        if locked.reservation_released:
            raise ValueError("رزرو این سفارش قبلاً منقضی یا آزاد شده است.")
        if locked.reservation_expires_at and timezone.now() >= locked.reservation_expires_at:
            raise ValueError("مهلت ۴۵ دقیقه‌ای رزرو این سفارش تمام شده است.")

        for item in locked.items.all():
            if not item.product_id:
                continue
            product = Product.objects.select_for_update().get(pk=item.product_id)
            if product.stock < item.quantity:
                raise ValueError(f"موجودی واقعی «{item.title}» برای نهایی‌کردن سفارش کافی نیست.")
            product.stock -= item.quantity
            product.reserved_stock = max(0, product.reserved_stock - item.quantity)
            fields = ["stock", "reserved_stock"]
            if product.manual_stock_override is not None:
                product.manual_stock_override = product.stock
                fields.append("manual_stock_override")
            product.save(update_fields=fields)

        locked.stock_committed = True
        locked.reservation_released = True
        locked.save(update_fields=["stock_committed", "reservation_released", "updated_at"])
        order.stock_committed = True
        order.reservation_released = True
        return True


def mark_paid(order):
    commit_order_stock(order)
    order.payment_status = Order.PAY_PAID
    order.status = "preparing"
    order.paid_at = order.paid_at or timezone.now()
    order.receipt_rejection_reason = ""
    order.stock_committed = True
    order.reservation_released = True
    order.save(update_fields=["payment_status", "status", "paid_at", "receipt_rejection_reason", "stock_committed", "reservation_released", "updated_at"])


def expire_reservations(limit=200):
    now = timezone.now()
    ids = list(
        Order.objects.filter(
            stock_committed=False,
            reservation_released=False,
            reservation_expires_at__isnull=False,
            reservation_expires_at__lte=now,
            status__in=["payment_pending", "receipt_pending", "cancelled", "payment_rejected"],
        ).values_list("id", flat=True)[:limit]
    )
    expired = []
    for oid in ids:
        try:
            order = Order.objects.select_related("user").get(pk=oid)
            if not release_order_stock(order):
                continue
            if order.payment_status != Order.PAY_PAID:
                order.payment_status = Order.PAY_FAILED
            order.status = "cancelled"
            order.admin_note = ((order.admin_note or "") + "\n" + RESERVATION_EXPIRED_MARKER).strip()
            order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
            email_customer(
                order,
                f"لغو سفارش #{order.id}",
                "مهلت ۴۵ دقیقه‌ای رزرو سفارش شما به پایان رسید و سفارش لغو شد. موجودی رزروشده آزاد شد.",
            )
            expired.append(order.id)
        except Order.DoesNotExist:
            continue
    return expired


def order_products_text(order):
    lines = []
    for item in order.items.select_related("product").all():
        code = item.product.public_code if item.product else "-"
        lines.append(f"• {item.title}\n  کد: {code} | تعداد: {item.quantity} | مبلغ: {item.total:,} تومان")
    return "\n".join(lines)


def order_report_text(order, title="🧾 فاکتور جدید"):
    created = timezone.localtime(order.created_at).strftime("%Y/%m/%d - %H:%M:%S")
    paid = timezone.localtime(order.paid_at).strftime("%Y/%m/%d - %H:%M:%S") if order.paid_at else "-"
    reserved_until = timezone.localtime(order.reservation_expires_at).strftime("%Y/%m/%d - %H:%M:%S") if order.reservation_expires_at else "-"
    receipt_time = ""
    if order.receipt and order.payment_status == Order.PAY_RECEIPT:
        receipt_time = timezone.localtime(order.updated_at).strftime("%Y/%m/%d - %H:%M:%S")
    time_lines = f"زمان ساخت فاکتور: {created}\nرزرو موجودی تا: {reserved_until}\n"
    if receipt_time:
        time_lines += f"زمان ارسال رسید: {receipt_time}\n"
    time_lines += f"زمان تایید پرداخت: {paid}"
    return (
        f"{title}\n"
        f"سفارش: #{order.id}\n"
        f"مشتری: {order.user.customer_code or '-'}\n"
        f"روش پرداخت: {order.get_payment_method_display()}\n"
        f"وضعیت پرداخت: {order.get_payment_status_display()}\n"
        f"مبلغ کالاها: {order.subtotal:,} تومان\n"
        f"تخفیف: {order.discount_amount:,} تومان"
        + (f" ({order.discount_code})" if order.discount_code else "")
        + f"\nبسته‌بندی: {order.packaging_cost:,} تومان\n"
        f"ارسال: {order.shipping_cost:,} تومان\n"
        f"مبلغ نهایی: {order.total:,} تومان\n\n"
        f"👤 {order.first_name} {order.last_name}\n"
        f"📧 {order.user.email}\n"
        f"📞 {order.phone}\n"
        f"📍 {order.province}، {order.city}\n{order.address}\n"
        f"📮 کد پستی: {order.postal_code or '-'}\n"
        + (f"📝 یادداشت: {order.order_note}\n" if order.order_note else "")
        + f"\n📦 محصولات:\n{order_products_text(order)}\n\n"
        + time_lines
        + (f"\nشماره تراکنش: {order.zarinpal_ref_id}" if order.zarinpal_ref_id else "")
        + (f"\n۴ رقم آخر کارت: {order.card_last4}" if order.card_last4 else "")
    )


def email_customer(order, subject, body):
    email = getattr(order.user, "email", "")
    if not email:
        return
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)
    except Exception:
        pass
