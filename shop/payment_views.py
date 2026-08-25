from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect

from .models import Order
from .views import card_payment as card_payment_flow


@login_required
def card_payment(request, pk):
    order = get_object_or_404(request.user.orders.only("id", "payment_status", "status", "payment_method"), pk=pk, payment_method=Order.PAYMENT_CARD)
    if order.payment_status in (Order.PAY_REJECTED, Order.PAY_FAILED) or order.status == "cancelled":
        messages.error(request, "این فاکتور بسته شده است. برای پرداخت مجدد یک سفارش جدید ثبت کنید.")
        return redirect("account_order_detail", pk=order.pk)
    return card_payment_flow(request, pk)
