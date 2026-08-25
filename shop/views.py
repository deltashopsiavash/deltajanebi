from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AccountProfileForm, CheckoutForm, RegisterForm
from .models import Banner, Category, Order, OrderItem, Product, SiteSetting
from .services.telegram_notify import notify_admins


def health(request):
    Product.objects.only("id").first()
    return JsonResponse({"ok": True})


def home(request):
    offer_candidates = list(
        Product.objects.filter(is_active=True, sale_price__isnull=False)
        .select_related("category")
        .order_by("sale_ends_at")[:30]
    )
    offers = [product for product in offer_candidates if product.is_sale_active][:12]
    return render(
        request,
        "shop/home.html",
        {
            # ناموجودها هم باید در فروشگاه دیده شوند؛ جدیدترین‌ها فقط ۱۰ مورد است.
            "products": Product.objects.filter(is_active=True).select_related("category")[:10],
            "offers": offers,
            "categories": Category.objects.filter(is_active=True, parent__isnull=True)[:12],
            "banners": Banner.objects.filter(is_active=True).exclude(image="", image_url="")[:8],
        },
    )


def search(request):
    q = request.GET.get("q", "").strip()
    qs = Product.objects.filter(is_active=True).select_related("category")
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(public_code__iexact=q)
            | Q(sku__icontains=q)
            | Q(description__icontains=q)
        )
    return render(
        request,
        "shop/list.html",
        {"products": qs[:100], "title": f"جستجو: {q}" if q else "همه محصولات"},
    )


def category(request, slug):
    cat = get_object_or_404(Category, slug=slug, is_active=True)
    category_ids = cat.descendant_ids()
    products = Product.objects.filter(category_id__in=category_ids, is_active=True).select_related("category")
    return render(
        request,
        "shop/list.html",
        {
            "products": products,
            "title": cat.name,
            "category": cat,
            "child_categories": cat.children.filter(is_active=True),
            "breadcrumbs": cat.ancestor_chain(),
        },
    )


def product_detail(request, slug):
    product = get_object_or_404(Product.objects.select_related("category__parent"), slug=slug, is_active=True)
    breadcrumbs = product.category.ancestor_chain() if product.category else []
    gallery_images = product.gallery_images()
    return render(
        request,
        "shop/product_detail.html",
        {
            "product": product,
            "breadcrumbs": breadcrumbs,
            "gallery_images": gallery_images,
            "feature_specs": list((product.specs or {}).items())[:3],
        },
    )


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
    return render(
        request,
        "shop/account_profile.html",
        {
            "form": form,
            "orders_count": request.user.orders.count(),
            "last_order": request.user.orders.first(),
        },
    )


def _cart(request):
    return request.session.setdefault("cart", {})


def cart_add(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    cart = _cart(request)
    key = str(product.id)
    cart[key] = min(product.stock, int(cart.get(key, 0)) + 1)
    request.session.modified = True
    messages.success(request, "محصول به سبد اضافه شد.")
    return redirect(request.POST.get("next") or product.get_absolute_url())


def cart_set(request, product_id):
    product = get_object_or_404(Product, pk=product_id)
    try:
        qty = max(0, min(product.stock, int(request.POST.get("qty", 0))))
    except ValueError:
        qty = 0
    cart = _cart(request)
    key = str(product.id)
    if qty:
        cart[key] = qty
    else:
        cart.pop(key, None)
    request.session.modified = True
    return redirect("cart")


def _cart_lines(request):
    cart = _cart(request)
    products = Product.objects.filter(id__in=cart.keys(), is_active=True)
    lines = []
    subtotal = 0
    for product in products:
        qty = min(int(cart.get(str(product.id), 0)), product.stock)
        unit_price = product.effective_price
        total = unit_price * qty
        subtotal += total
        lines.append({"product": product, "qty": qty, "unit_price": unit_price, "total": total})
    return lines, subtotal


def cart_view(request):
    lines, subtotal = _cart_lines(request)
    settings = SiteSetting.load()
    return render(
        request,
        "shop/cart.html",
        {"lines": lines, "subtotal": subtotal, "total": subtotal + (settings.shipping_cost if lines else 0)},
    )


@login_required
def checkout(request):
    lines, subtotal = _cart_lines(request)
    if not lines:
        return redirect("cart")
    settings = SiteSetting.load()
    form = CheckoutForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked = []
            locked_subtotal = 0
            for line in lines:
                product = Product.objects.select_for_update().get(pk=line["product"].pk)
                if product.stock < line["qty"]:
                    messages.error(request, f"موجودی {product.name} تغییر کرده است.")
                    return redirect("cart")
                unit_price = product.effective_price
                locked_subtotal += unit_price * line["qty"]
                locked.append((product, line["qty"], unit_price))
            order = Order.objects.create(
                user=request.user,
                subtotal=locked_subtotal,
                shipping_cost=settings.shipping_cost,
                total=locked_subtotal + settings.shipping_cost,
                **form.cleaned_data,
            )
            for product, qty, unit_price in locked:
                OrderItem.objects.create(order=order, product=product, title=product.name, price=unit_price, quantity=qty)
                product.stock -= qty
                product.save(update_fields=["stock"])
            request.session["cart"] = {}
            request.session.modified = True
        notify_admins(
            f"🛒 سفارش جدید #{order.id}\nمبلغ: {order.total:,} تومان\nکاربر: {order.user.email}"
        )
        messages.success(request, "سفارش ثبت شد و رسید در انتظار تایید است.")
        return redirect("account_order_detail", pk=order.pk)
    return render(
        request,
        "shop/checkout.html",
        {
            "form": form,
            "lines": lines,
            "subtotal": subtotal,
            "settings": settings,
            "total": subtotal + settings.shipping_cost,
        },
    )


@login_required
def account_orders(request):
    return render(request, "shop/orders.html", {"orders": request.user.orders.all()})


@login_required
def account_order_detail(request, pk):
    return render(
        request,
        "shop/order_detail.html",
        {"order": get_object_or_404(request.user.orders.prefetch_related("items"), pk=pk)},
    )
