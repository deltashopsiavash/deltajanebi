from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from shop.models import Order, Product

STOCK_RELEASE_MARKER = "[stock_released]"


def release_order_stock(order):
    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related("items__product").get(pk=order.pk)
        if STOCK_RELEASE_MARKER in (locked.admin_note or ""):
            return False
        for item in locked.items.all():
            if item.product_id:
                product = Product.objects.select_for_update().filter(pk=item.product_id).first()
                if product:
                    product.stock += item.quantity
                    if product.manual_stock_override is not None:
                        product.manual_stock_override = product.stock
                        product.save(update_fields=["stock", "manual_stock_override"])
                    else:
                        product.save(update_fields=["stock"])
        locked.admin_note = ((locked.admin_note or "") + "\n" + STOCK_RELEASE_MARKER).strip()
        locked.save(update_fields=["admin_note"])
        order.admin_note = locked.admin_note
        return True


def mark_paid(order):
    order.payment_status = Order.PAY_PAID
    order.status = "preparing"
    order.paid_at = order.paid_at or timezone.now()
    order.receipt_rejection_reason = ""
    order.save(update_fields=["payment_status", "status", "paid_at", "receipt_rejection_reason", "updated_at"])


def order_products_text(order):
    lines = []
    for item in order.items.select_related("product").all():
        code = item.product.public_code if item.product else "-"
        lines.append(f"• {item.title}\n  کد: {code} | تعداد: {item.quantity} | مبلغ: {item.total:,} تومان")
    return "\n".join(lines)


def order_report_text(order, title="🧾 فاکتور جدید"):
    created = timezone.localtime(order.created_at).strftime("%Y/%m/%d - %H:%M:%S")
    paid = timezone.localtime(order.paid_at).strftime("%Y/%m/%d - %H:%M:%S") if order.paid_at else "-"
    receipt_time = ""
    if order.receipt and order.payment_status == Order.PAY_RECEIPT:
        receipt_time = timezone.localtime(order.updated_at).strftime("%Y/%m/%d - %H:%M:%S")
    time_lines = f"زمان ساخت فاکتور: {created}\n"
    if receipt_time:
        time_lines += f"زمان ارسال رسید: {receipt_time}\n"
    time_lines += f"زمان تایید پرداخت: {paid}"
    return (
        f"{title}\n"
        f"سفارش: #{order.id}\n"
        f"روش پرداخت: {order.get_payment_method_display()}\n"
        f"وضعیت پرداخت: {order.get_payment_status_display()}\n"
        f"مبلغ کالاها: {order.subtotal:,} تومان\n"
        f"تخفیف: {order.discount_amount:,} تومان"
        + (f" ({order.discount_code})" if order.discount_code else "")
        + f"\nبسته‌بندی: {order.packaging_cost:,} تومان\n"
        f"ارسال: {order.shipping_cost:,} تومان\n"
        f"مبلغ نهایی: {order.total:,} تومان\n\n"
        f"👤 {order.first_name} {order.last_name}\n"
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
