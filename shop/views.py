from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import AccountProfileForm, CheckoutForm, ReceiptUploadForm, RegisterForm
from .iran_locations import province_city_map
from .models import Banner, Category, DiscountCode, Order, OrderItem, Product, SiteSetting
from .services.order_workflow import email_customer, mark_paid, order_report_text, release_order_stock
from .services.payments import PaymentError, request_zarinpal_payment, verify_zarinpal_payment
from .services.telegram_notify import notify_admins, notify_admins_photo


def health(request):
    Product.objects.only("id").first()
    return JsonResponse({"ok": True})


def _catalog_queryset():
    qs = Product.objects.filter(is_active=True).select_related("category")
    if SiteSetting.load().hide_out_of_stock:
        qs = qs.filter(stock__gt=0)
    return qs


def home(request):
    offer_candidates = list(Product.objects.filter(is_active=True, stock__gt=0, sale_price__isnull=False).select_related("category").order_by("sale_ends_at")[:30])
    offers = [product for product in offer_candidates if product.is_sale_active][:12]
    return render(request, "shop/home.html", {
        "products": Product.objects.filter(is_active=True, stock__gt=0).select_related("category")[:10],
        "offers": offers,
        "categories": Category.objects.filter(is_active=True, parent__isnull=True)[:12],
        "banners": Banner.objects.filter(is_active=True).exclude(image="", image_url="")[:8],
    })


def search(request):
    q = request.GET.get("q", "").strip()
    qs = _catalog_queryset()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(public_code__iexact=q) | Q(sku__icontains=q) | Q(description__icontains=q))
    return render(request, "shop/list.html", {"products": qs[:100], "title": f"جستجو: {q}" if q else "همه محصولات"})


def category(request, slug):
    cat = get_object_or_404(Category, slug=slug, is_active=True)
    products = _catalog_queryset().filter(category_id__in=cat.descendant_ids())
    return render(request, "shop/list.html", {"products": products, "title": cat.name, "category": cat, "child_categories": cat.children.filter(is_active=True), "breadcrumbs": cat.ancestor_chain()})


def product_detail(request, slug):
    qs = Product.objects.select_related("category__parent").filter(is_active=True)
    if SiteSetting.load().hide_out_of_stock:
        qs = qs.filter(stock__gt=0)
    product = get_object_or_404(qs, slug=slug)
    return render(request, "shop/product_detail.html", {"product": product, "breadcrumbs": product.category.ancestor_chain() if product.category else [], "gallery_images": product.gallery_images(), "feature_specs": list((product.specs or {}).items())[:3]})


def register(request):
    if request.user.is_authenticated:
        return redirect("account_profile")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("account_profile")
    return render(request, "registration/register.html", {"form": form})


@login_required
def account_profile(request):
    form = AccountProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "مشخصات حساب کاربری با موفقیت ذخیره شد.")
        return redirect("account_profile")
    return render(request, "shop/account_profile.html", {"form": form, "orders_count": request.user.orders.count(), "last_order": request.user.orders.first()})


def _wants_json(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")


def _cart(request):
    cart = request.session.get("cart")
    if not isinstance(cart, dict):
        cart = {}
        request.session["cart"] = cart
    return cart


def _normalize_cart(request):
    cart = _cart(request)
    ids = [int(key) for key in cart.keys() if str(key).isdigit()]
    products = {p.id: p for p in Product.objects.filter(id__in=ids, is_active=True)}
    clean = {}
    for pid, product in products.items():
        try:
            qty = max(0, min(int(cart.get(str(pid), 0)), product.stock))
        except (TypeError, ValueError):
            qty = 0
        if qty > 0:
            clean[str(pid)] = qty
    if clean != cart:
        request.session["cart"] = clean
        request.session.modified = True
    return clean, products


def _active_discount(request, subtotal):
    code = str(request.session.get("discount_code") or "").strip().upper()
    if not code:
        return None, 0
    discount = DiscountCode.objects.filter(code__iexact=code).first()
    if not discount or not discount.is_valid_now:
        request.session.pop("discount_code", None)
        request.session.modified = True
        return None, 0
    return discount, discount.calculate(subtotal)


def _cart_totals(request):
    cart, products = _normalize_cart(request)
    lines, subtotal = [], 0
    for key, qty in cart.items():
        product = products.get(int(key))
        if not product:
            continue
        unit_price = product.effective_price
        line_total = unit_price * qty
        subtotal += line_total
        lines.append({"product": product, "qty": qty, "unit_price": unit_price, "total": line_total})
    settings = SiteSetting.load()
    discount, discount_amount = _active_discount(request, subtotal)
    packaging = settings.packaging_cost if lines else 0
    shipping = settings.shipping_for(subtotal) if lines else 0
    total = max(0, subtotal - discount_amount) + packaging + shipping
    remaining = max(0, settings.free_shipping_threshold - subtotal) if settings.free_shipping_threshold else 0
    return {"lines": lines, "subtotal": subtotal, "discount": discount, "discount_amount": discount_amount, "packaging": packaging, "shipping": shipping, "total": total, "free_shipping_threshold": settings.free_shipping_threshold, "remaining_to_free_shipping": remaining, "free_shipping": bool(lines and settings.free_shipping_threshold and subtotal >= settings.free_shipping_threshold), "settings": settings, "cart_count": sum(line["qty"] for line in lines)}


def _cart_json(request):
    data = _cart_totals(request)
    return {
        "count": data["cart_count"], "subtotal": data["subtotal"], "discount_amount": data["discount_amount"],
        "discount_code": data["discount"].code if data["discount"] else "", "packaging": data["packaging"],
        "shipping": data["shipping"], "total": data["total"], "free_shipping": data["free_shipping"],
        "free_shipping_threshold": data["free_shipping_threshold"], "remaining_to_free_shipping": data["remaining_to_free_shipping"],
        "lines": [{"id": l["product"].id, "name": l["product"].name, "code": l["product"].public_code or "", "image": l["product"].primary_image or "", "url": l["product"].get_absolute_url(), "qty": l["qty"], "stock": l["product"].stock, "unit_price": l["unit_price"], "total": l["total"]} for l in data["lines"]],
    }


def cart_add(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    if request.method != "POST":
        return redirect(product.get_absolute_url())
    if product.stock <= 0:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "این محصول ناموجود است."}, status=400)
        messages.error(request, "این محصول ناموجود است.")
        return redirect(request.POST.get("next") or product.get_absolute_url())
    cart = _cart(request)
    key = str(product.id)
    cart[key] = min(product.stock, int(cart.get(key, 0)) + 1)
    request.session["cart"] = cart
    request.session.modified = True
    if _wants_json(request):
        payload = _cart_json(request)
        payload.update({"ok": True, "added": {"name": product.name, "image": product.primary_image or "", "code": product.public_code or ""}})
        return JsonResponse(payload)
    messages.success(request, "محصول به سبد اضافه شد.")
    return redirect(request.POST.get("next") or product.get_absolute_url())


def cart_set(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    try:
        qty = max(0, min(product.stock, int(request.POST.get("qty", 0))))
    except (TypeError, ValueError):
        qty = 0
    cart = _cart(request)
    if qty:
        cart[str(product.id)] = qty
    else:
        cart.pop(str(product.id), None)
    request.session["cart"] = cart
    request.session.modified = True
    if _wants_json(request):
        return JsonResponse({"ok": True, **_cart_json(request)})
    return redirect("cart")


def cart_data(request):
    return JsonResponse({"ok": True, **_cart_json(request)})


def cart_discount(request):
    code = str(request.POST.get("code") or "").strip().upper()
    discount = DiscountCode.objects.filter(code__iexact=code).first()
    if not discount or not discount.is_valid_now:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "کد تخفیف معتبر نیست."}, status=400)
        messages.error(request, "کد تخفیف معتبر نیست.")
        return redirect("cart")
    request.session["discount_code"] = discount.code
    request.session.modified = True
    if _wants_json(request):
        return JsonResponse({"ok": True, **_cart_json(request)})
    messages.success(request, "کد تخفیف اعمال شد.")
    return redirect("cart")


def cart_discount_remove(request):
    request.session.pop("discount_code", None)
    request.session.modified = True
    if _wants_json(request):
        return JsonResponse({"ok": True, **_cart_json(request)})
    return redirect("cart")


def cart_view(request):
    return render(request, "shop/cart.html", _cart_totals(request))


def _reserve_order(request, form, totals):
    settings = totals["settings"]
    with transaction.atomic():
        locked_lines, locked_subtotal = [], 0
        for line in totals["lines"]:
            product = Product.objects.select_for_update().get(pk=line["product"].pk)
            if not product.is_active or product.stock < line["qty"]:
                raise ValueError(f"موجودی «{product.name}» تغییر کرده است.")
            unit_price = product.effective_price
            locked_subtotal += unit_price * line["qty"]
            locked_lines.append((product, line["qty"], unit_price))
        discount = totals["discount"]
        discount_amount = discount.calculate(locked_subtotal) if discount else 0
        shipping = settings.shipping_for(locked_subtotal)
        packaging = settings.packaging_cost
        final_total = max(0, locked_subtotal - discount_amount) + shipping + packaging
        cd = form.cleaned_data
        order = Order.objects.create(user=request.user, status="payment_pending", first_name=cd["first_name"], last_name=cd["last_name"], full_name=f"{cd['first_name']} {cd['last_name']}".strip(), phone=cd["phone"], province=cd["province"], city=cd["city"], address=cd["address"], order_note=cd.get("order_note", ""), subtotal=locked_subtotal, discount_code=discount.code if discount else "", discount_amount=discount_amount, packaging_cost=packaging, shipping_cost=shipping, total=final_total, payment_method=cd["payment_method"], payment_status=Order.PAY_PENDING)
        for product, qty, unit_price in locked_lines:
            OrderItem.objects.create(order=order, product=product, title=product.name, price=unit_price, quantity=qty)
            product.stock -= qty
            product.save(update_fields=["stock"])
        request.session["cart"] = {}
        request.session.pop("discount_code", None)
        request.session.modified = True
    return order


def _absolute(request, value):
    value = str(value or "")
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return request.build_absolute_uri(value)


def _notify_invoice(request, order):
    notify_admins(order_report_text(order, "🧾 فاکتور ساخته شد"))
    for item in order.items.select_related("product").all():
        if item.product and item.product.primary_image:
            notify_admins_photo(_absolute(request, item.product.primary_image), f"📦 {item.title}\nکد: {item.product.public_code or '-'}\nتعداد: {item.quantity}\nسفارش: #{order.id}")


@login_required
def checkout(request):
    totals = _cart_totals(request)
    if not totals["lines"]:
        return redirect("cart")
    settings = totals["settings"]
    form = CheckoutForm(request.POST or None, settings=settings, initial={"first_name": request.user.first_name, "last_name": request.user.last_name, "phone": request.user.phone})
    if request.method == "POST" and form.is_valid():
        try:
            order = _reserve_order(request, form, totals)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("cart")
        _notify_invoice(request, order)
        if order.payment_method == Order.PAYMENT_CARD:
            return redirect("card_payment", pk=order.pk)
        try:
            authority, gateway_url = request_zarinpal_payment(merchant_id=settings.zarinpal_merchant_id, amount_toman=order.total, callback_url=request.build_absolute_uri(reverse("zarinpal_callback")), description=f"سفارش #{order.id} از {settings.store_name}", mobile=order.phone, email=request.user.email)
            order.zarinpal_authority = authority
            order.save(update_fields=["zarinpal_authority", "updated_at"])
            return redirect(gateway_url)
        except PaymentError as exc:
            order.payment_status = Order.PAY_FAILED
            order.status = "cancelled"
            order.admin_note = f"خطای شروع زرین‌پال: {exc}"
            order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
            release_order_stock(order)
            notify_admins(f"⚠️ شروع پرداخت زرین‌پال سفارش #{order.id} ناموفق بود:\n{exc}")
            messages.error(request, str(exc))
            return redirect("account_order_detail", pk=order.pk)
    return render(request, "shop/checkout.html", {**totals, "form": form, "locations": province_city_map()})


@login_required
def card_payment(request, pk):
    order = get_object_or_404(request.user.orders.prefetch_related("items__product"), pk=pk, payment_method=Order.PAYMENT_CARD)
    if order.payment_status == Order.PAY_PAID:
        return redirect("account_order_detail", pk=order.pk)
    form = ReceiptUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        order.receipt = form.cleaned_data["receipt"]
        order.payment_status = Order.PAY_RECEIPT
        order.status = "receipt_pending"
        order.receipt_rejection_reason = ""
        order.save(update_fields=["receipt", "payment_status", "status", "receipt_rejection_reason", "updated_at"])
        buttons = {"inline_keyboard": [[{"text": "✅ تایید رسید", "callback_data": f"receipt:approve:{order.id}"}, {"text": "❌ رد رسید", "callback_data": f"receipt:reject:{order.id}"}]]}
        report = order_report_text(order, "🧾 رسید کارت‌به‌کارت جدید")
        notify_admins_photo(_absolute(request, order.receipt.url), report[:1000], reply_markup=buttons)
        if len(report) > 1000:
            notify_admins(report)
        messages.success(request, "رسید با موفقیت ارسال شد و در انتظار بررسی است.")
        return redirect("account_order_detail", pk=order.pk)
    return render(request, "shop/card_payment.html", {"order": order, "form": form, "settings": SiteSetting.load()})


def zarinpal_callback(request):
    authority = str(request.GET.get("Authority") or "").strip()
    status = str(request.GET.get("Status") or "").upper()
    order = Order.objects.filter(zarinpal_authority=authority, payment_method=Order.PAYMENT_ZARINPAL).select_related("user").prefetch_related("items__product").first()
    if not order:
        messages.error(request, "سفارش مربوط به این پرداخت پیدا نشد.")
        return redirect("home")
    if order.payment_status == Order.PAY_PAID:
        messages.success(request, "این پرداخت قبلاً با موفقیت تایید شده است.")
        return redirect("account_order_detail", pk=order.pk)
    if status != "OK":
        order.payment_status = Order.PAY_FAILED
        order.status = "cancelled"
        order.save(update_fields=["payment_status", "status", "updated_at"])
        release_order_stock(order)
        notify_admins(f"❌ پرداخت زرین‌پال سفارش #{order.id} لغو/ناموفق شد.")
        messages.error(request, "پرداخت انجام نشد یا توسط کاربر لغو شد.")
        return redirect("account_order_detail", pk=order.pk)
    try:
        settings = SiteSetting.load()
        result = verify_zarinpal_payment(merchant_id=settings.zarinpal_merchant_id, amount_toman=order.total, authority=authority)
        order.zarinpal_ref_id = result["ref_id"]
        order.zarinpal_card_pan = result["card_pan"]
        mark_paid(order)
        order.save(update_fields=["zarinpal_ref_id", "zarinpal_card_pan", "updated_at"])
        notify_admins(order_report_text(order, "✅ پرداخت زرین‌پال موفق"))
        email_customer(order, f"پرداخت سفارش #{order.id} موفق بود", f"پرداخت سفارش شما با شماره تراکنش {order.zarinpal_ref_id} با موفقیت تایید شد.")
        messages.success(request, f"پرداخت با موفقیت انجام شد. شماره تراکنش: {order.zarinpal_ref_id}")
    except PaymentError as exc:
        order.payment_status = Order.PAY_FAILED
        order.status = "cancelled"
        order.admin_note = f"خطای تایید زرین‌پال: {exc}"
        order.save(update_fields=["payment_status", "status", "admin_note", "updated_at"])
        release_order_stock(order)
        notify_admins(f"❌ تایید پرداخت زرین‌پال سفارش #{order.id} ناموفق بود:\n{exc}")
        messages.error(request, str(exc))
    return redirect("account_order_detail", pk=order.pk)


def terms(request):
    return render(request, "shop/terms.html", {"settings": SiteSetting.load()})


@login_required
def account_orders(request):
    return render(request, "shop/orders.html", {"orders": request.user.orders.all()})


@login_required
def account_order_detail(request, pk):
    return render(request, "shop/order_detail.html", {"order": get_object_or_404(request.user.orders.prefetch_related("items"), pk=pk)})
