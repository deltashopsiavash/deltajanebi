from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import CheckoutForm, ReceiptUploadForm
from .iran_locations import province_city_map
from .models import Order, OrderItem, Product, SiteSetting
from .services.order_workflow import email_customer, mark_paid, order_report_text, release_order_stock, reservation_deadline
from .services.payments import PaymentError, request_zarinpal_payment, verify_zarinpal_payment
from .services.telegram_notify import notify_admins, notify_admins_photo
from .services.wallet import apply_wallet_to_order, external_payable, order_wallet_info, refund_order_wallet, wallet_balance
from .views import _absolute, _cart_totals


def _notify_order(request, order, title):
    notify_admins(order_report_text(order, title))
    for item in order.items.select_related("product").all():
        if item.product and item.product.primary_image:
            notify_admins_photo(
                _absolute(request, item.product.primary_image),
                f"📦 {item.title}\nکد: {item.product.public_code or '-'}\nتعداد: {item.quantity}\nسفارش: #{order.id}",
            )


def _reserve_order(request, form, totals):
    settings = totals["settings"]
    with transaction.atomic():
        locked_lines, locked_subtotal = [], 0
        for line in totals["lines"]:
            product = Product.objects.select_for_update().get(pk=line["product"].pk)
            available = max(0, product.stock - product.reserved_stock)
            if not product.is_active or available < line["qty"]:
                raise ValueError(f"موجودی آزاد «{product.name}» تغییر کرده است.")
            unit_price = product.effective_price
            locked_subtotal += unit_price * line["qty"]
            locked_lines.append((product, line["qty"], unit_price))

        discount = totals["discount"]
        discount_amount = discount.calculate(locked_subtotal) if discount else 0
        shipping = settings.shipping_for(locked_subtotal)
        packaging = settings.packaging_cost
        final_total = max(0, locked_subtotal - discount_amount) + shipping + packaging
        cd = form.cleaned_data
        chosen_method = cd.get("payment_method") or "wallet"

        order = Order.objects.create(
            user=request.user,
            status="payment_pending",
            first_name=cd["first_name"],
            last_name=cd["last_name"],
            full_name=f"{cd['first_name']} {cd['last_name']}".strip(),
            phone=cd["phone"],
            province=cd["province"],
            city=cd["city"],
            address=cd["address"],
            postal_code=cd["postal_code"],
            order_note=cd.get("order_note", ""),
            subtotal=locked_subtotal,
            discount_code=discount.code if discount else "",
            discount_amount=discount_amount,
            packaging_cost=packaging,
            shipping_cost=shipping,
            total=final_total,
            payment_method=chosen_method,
            payment_status=Order.PAY_PENDING,
            reservation_expires_at=reservation_deadline(),
            stock_committed=False,
            reservation_released=False,
        )

        for product, qty, unit_price in locked_lines:
            OrderItem.objects.create(order=order, product=product, title=product.name, price=unit_price, quantity=qty)
            product.reserved_stock += qty
            product.save(update_fields=["reserved_stock"])

        if cd.get("use_wallet"):
            apply_wallet_to_order(order, requested=True)

        due = external_payable(order)
        if due > 0 and chosen_method == "wallet":
            raise ValueError("موجودی کیف پول در لحظه ثبت سفارش تغییر کرده است؛ روش پرداخت باقی‌مانده را دوباره انتخاب کنید.")

        request.session["cart"] = {}
        request.session.pop("discount_code", None)
        request.session.modified = True
    return order


@login_required
def checkout(request):
    totals = _cart_totals(request)
    if not totals["lines"]:
        return redirect("cart")

    settings = totals["settings"]
    balance = wallet_balance(request.user.pk)
    form = CheckoutForm(
        request.POST or None,
        settings=settings,
        wallet_balance=balance,
        order_total=totals["total"],
        initial={
            "first_name": request.user.first_name,
            "last_name": request.user.last_name,
            "phone": request.user.phone,
        },
    )

    if request.method == "POST" and form.is_valid():
        try:
            order = _reserve_order(request, form, totals)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("checkout")

        due = external_payable(order)
        wallet_info = order_wallet_info(order.id)

        if due == 0:
            try:
                mark_paid(order)
            except ValueError as exc:
                release_order_stock(order)
                refund_order_wallet(order, f"برگشت کیف پول سفارش #{order.id} به علت خطای نهایی‌سازی")
                order.status = "cancelled"
                order.payment_status = Order.PAY_FAILED
                order.admin_note = str(exc)
                order.save(update_fields=["status", "payment_status", "admin_note", "updated_at"])
                messages.error(request, "نهایی‌سازی سفارش انجام نشد و مبلغ کیف پول به حساب شما برگشت.")
                return redirect("account_order_detail", pk=order.pk)
            _notify_order(request, order, "✅ پرداخت کامل سفارش از کیف پول")
            email_customer(order, f"پرداخت سفارش #{order.id} موفق بود", "کل مبلغ سفارش از موجودی کیف پول شما پرداخت شد و سفارش وارد مرحله آماده‌سازی شد.")
            messages.success(request, f"پرداخت موفق بود؛ {wallet_info['wallet_amount']:,} تومان از کیف پول شما کسر شد.")
            return redirect("account_order_detail", pk=order.pk)

        _notify_order(request, order, "🧾 فاکتور ساخته شد — پرداخت ترکیبی" if wallet_info["wallet_amount"] else "🧾 فاکتور ساخته شد — رزرو ۴۵ دقیقه‌ای")

        if order.payment_method == Order.PAYMENT_CARD:
            return redirect("card_payment", pk=order.pk)

        try:
            authority, gateway_url = request_zarinpal_payment(
                merchant_id=settings.zarinpal_merchant_id,
                amount_toman=due,
                callback_url=request.build_absolute_uri(reverse("zarinpal_callback")),
                description=f"سفارش #{order.id} از {settings.store_name}",
                mobile=order.phone,
                email=request.user.email,
            )
            order.zarinpal_authority = authority
            order.save(update_fields=["zarinpal_authority", "updated_at"])
            return redirect(gateway_url)
        except PaymentError as exc:
            order.payment_status = Order.PAY_FAILED
            order.status = "cancelled"
            order.admin_note = f"خطای شروع زرین‌پال: {exc}"
            order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
            release_order_stock(order)
            refund_order_wallet(order, f"برگشت کیف پول سفارش #{order.id} به علت شروع نشدن درگاه")
            notify_admins(order_report_text(order, "⚠️ شروع پرداخت زرین‌پال ناموفق بود"))
            messages.error(request, str(exc))
            return redirect("account_order_detail", pk=order.pk)

    return render(
        request,
        "shop/checkout.html",
        {**totals, "form": form, "locations": province_city_map(), "wallet_balance": balance},
    )


@login_required
def card_payment(request, pk):
    order = get_object_or_404(
        request.user.orders.prefetch_related("items__product"),
        pk=pk,
        payment_method=Order.PAYMENT_CARD,
    )
    if order.payment_status == Order.PAY_PAID:
        return redirect("account_order_detail", pk=order.pk)
    if order.payment_status in (Order.PAY_REJECTED, Order.PAY_FAILED) or order.status == "cancelled":
        messages.error(request, "این فاکتور بسته شده است. برای پرداخت مجدد یک سفارش جدید ثبت کنید.")
        return redirect("account_order_detail", pk=order.pk)
    if order.reservation_released or not order.reservation_active:
        messages.error(request, "مهلت رزرو این فاکتور تمام شده است. یک سفارش جدید ثبت کنید.")
        return redirect("account_order_detail", pk=order.pk)

    due = external_payable(order)
    wallet_info = order_wallet_info(order.id)
    form = ReceiptUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        order.receipt = form.cleaned_data["receipt"]
        order.payment_status = Order.PAY_RECEIPT
        order.status = "receipt_pending"
        order.receipt_rejection_reason = ""
        order.save(update_fields=["receipt", "payment_status", "status", "receipt_rejection_reason", "updated_at"])
        buttons = {"inline_keyboard": [[
            {"text": "✅ تایید رسید", "callback_data": f"receipt:approve:{order.id}"},
            {"text": "❌ رد رسید", "callback_data": f"receipt:reject:{order.id}"},
        ]]}
        report = order_report_text(order, "🧾 رسید کارت‌به‌کارت جدید")
        notify_admins_photo(_absolute(request, order.receipt.url), report[:1000], reply_markup=buttons)
        if len(report) > 1000:
            notify_admins(report)
        messages.success(request, "رسید با موفقیت ارسال شد و در انتظار بررسی است.")
        return redirect("account_order_detail", pk=order.pk)

    return render(
        request,
        "shop/card_payment.html",
        {"order": order, "form": form, "settings": SiteSetting.load(), "external_due": due, "wallet_info": wallet_info},
    )


def zarinpal_callback(request):
    authority = str(request.GET.get("Authority") or "").strip()
    status = str(request.GET.get("Status") or "").upper()
    order = Order.objects.filter(
        zarinpal_authority=authority,
        payment_method=Order.PAYMENT_ZARINPAL,
    ).select_related("user").prefetch_related("items__product").first()
    if not order:
        messages.error(request, "سفارش مربوط به این پرداخت پیدا نشد.")
        return redirect("home")
    if order.payment_status == Order.PAY_PAID and order.status != "cancelled":
        messages.success(request, "این پرداخت قبلاً با موفقیت تایید شده است.")
        return redirect("account_order_detail", pk=order.pk)

    due = external_payable(order)
    if status != "OK":
        order.payment_status = Order.PAY_FAILED
        order.status = "cancelled"
        order.save(update_fields=["payment_status", "status", "updated_at"])
        release_order_stock(order)
        refund_order_wallet(order, f"برگشت کیف پول سفارش #{order.id} به علت لغو پرداخت زرین‌پال")
        notify_admins(order_report_text(order, "❌ پرداخت زرین‌پال لغو/ناموفق شد"))
        messages.error(request, "پرداخت انجام نشد یا توسط کاربر لغو شد؛ سهم کیف پول نیز برگشت داده شد.")
        return redirect("account_order_detail", pk=order.pk)

    try:
        settings = SiteSetting.load()
        result = verify_zarinpal_payment(
            merchant_id=settings.zarinpal_merchant_id,
            amount_toman=due,
            authority=authority,
        )
        order.zarinpal_ref_id = result["ref_id"]
        order.zarinpal_card_pan = result["card_pan"]
        order.save(update_fields=["zarinpal_ref_id", "zarinpal_card_pan", "updated_at"])
        try:
            mark_paid(order)
        except ValueError as exc:
            refund_order_wallet(order, f"برگشت کیف پول سفارش #{order.id} به علت انقضای رزرو پس از پرداخت درگاه")
            order.payment_status = Order.PAY_PAID
            order.status = "cancelled"
            order.admin_note = ((order.admin_note or "") + f"\nپرداخت درگاه تایید شد ولی رزرو منقضی بود: {exc}").strip()
            order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
            notify_admins(order_report_text(order, "🚨 درگاه پرداخت شد ولی رزرو موجودی منقضی بود"))
            messages.error(request, "پرداخت درگاه ثبت شد اما رزرو موجودی منقضی شده بود؛ سهم کیف پول برگشت و پشتیبانی مبلغ درگاه را بررسی می‌کند.")
            return redirect("account_order_detail", pk=order.pk)
        notify_admins(order_report_text(order, "✅ پرداخت زرین‌پال موفق"))
        email_customer(order, f"پرداخت سفارش #{order.id} موفق بود", f"پرداخت سفارش شما با شماره تراکنش {order.zarinpal_ref_id} با موفقیت تایید شد.")
        messages.success(request, f"پرداخت با موفقیت انجام شد. شماره تراکنش: {order.zarinpal_ref_id}")
    except PaymentError as exc:
        order.payment_status = Order.PAY_FAILED
        order.status = "cancelled"
        order.admin_note = f"خطای تایید زرین‌پال: {exc}"
        order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
        release_order_stock(order)
        refund_order_wallet(order, f"برگشت کیف پول سفارش #{order.id} به علت خطای تایید زرین‌پال")
        notify_admins(order_report_text(order, "❌ تایید پرداخت زرین‌پال ناموفق بود"))
        messages.error(request, str(exc))
    return redirect("account_order_detail", pk=order.pk)
