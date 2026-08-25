from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from .forms import CheckoutForm,RegisterForm
from .models import Category,Order,OrderItem,Product,SiteSetting
from .services.telegram_notify import notify_admins

def health(request): return JsonResponse({"ok":True})
def home(request): return render(request,"shop/home.html",{"products":Product.objects.filter(is_active=True,stock__gt=0).select_related("category")[:24],"categories":Category.objects.filter(is_active=True)[:12]})
def search(request):
    q=request.GET.get("q","").strip(); qs=Product.objects.filter(is_active=True)
    if q: qs=qs.filter(Q(name__icontains=q)|Q(sku__icontains=q)|Q(description__icontains=q))
    return render(request,"shop/list.html",{"products":qs[:100],"title":f"جستجو: {q}" if q else "همه محصولات"})
def category(request,slug):
    cat=get_object_or_404(Category,slug=slug,is_active=True); return render(request,"shop/list.html",{"products":cat.products.filter(is_active=True),"title":cat.name})
def product_detail(request,slug): return render(request,"shop/product_detail.html",{"product":get_object_or_404(Product,slug=slug,is_active=True)})
def register(request):
    if request.user.is_authenticated: return redirect("home")
    form=RegisterForm(request.POST or None)
    if request.method=="POST" and form.is_valid(): user=form.save(); login(request,user); return redirect("home")
    return render(request,"registration/register.html",{"form":form})
def _cart(request): return request.session.setdefault("cart",{})
def cart_add(request,product_id):
    p=get_object_or_404(Product,pk=product_id,is_active=True); cart=_cart(request); key=str(p.id); cart[key]=min(p.stock,int(cart.get(key,0))+1); request.session.modified=True; messages.success(request,"محصول به سبد اضافه شد."); return redirect(request.POST.get("next") or p.get_absolute_url())
def cart_set(request,product_id):
    p=get_object_or_404(Product,pk=product_id)
    try: qty=max(0,min(p.stock,int(request.POST.get("qty",0))))
    except ValueError: qty=0
    cart=_cart(request); key=str(p.id)
    if qty: cart[key]=qty
    else: cart.pop(key,None)
    request.session.modified=True; return redirect("cart")
def _cart_lines(request):
    cart=_cart(request); products=Product.objects.filter(id__in=cart.keys(),is_active=True); lines=[]; subtotal=0
    for p in products:
        qty=min(int(cart.get(str(p.id),0)),p.stock); total=p.price*qty; subtotal+=total; lines.append({"product":p,"qty":qty,"total":total})
    return lines,subtotal
def cart_view(request):
    lines,subtotal=_cart_lines(request); s=SiteSetting.load(); return render(request,"shop/cart.html",{"lines":lines,"subtotal":subtotal,"total":subtotal+(s.shipping_cost if lines else 0)})
@login_required
def checkout(request):
    lines,subtotal=_cart_lines(request)
    if not lines: return redirect("cart")
    s=SiteSetting.load(); form=CheckoutForm(request.POST or None,request.FILES or None)
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            locked=[]
            for line in lines:
                p=Product.objects.select_for_update().get(pk=line["product"].pk)
                if p.stock<line["qty"]: messages.error(request,f"موجودی {p.name} تغییر کرده است."); return redirect("cart")
                locked.append((p,line["qty"]))
            order=Order.objects.create(user=request.user,subtotal=subtotal,shipping_cost=s.shipping_cost,total=subtotal+s.shipping_cost,**form.cleaned_data)
            for p,qty in locked:
                OrderItem.objects.create(order=order,product=p,title=p.name,price=p.price,quantity=qty); p.stock-=qty; p.save(update_fields=["stock"])
            request.session["cart"]={}; request.session.modified=True
        notify_admins(f"🛒 سفارش جدید #{order.id}\nمبلغ: {order.total:,} تومان\nکاربر: {order.user.email}"); messages.success(request,"سفارش ثبت شد و رسید در انتظار تایید است."); return redirect("account_order_detail",pk=order.pk)
    return render(request,"shop/checkout.html",{"form":form,"lines":lines,"subtotal":subtotal,"settings":s,"total":subtotal+s.shipping_cost})
@login_required
def account_orders(request): return render(request,"shop/orders.html",{"orders":request.user.orders.all()})
@login_required
def account_order_detail(request,pk): return render(request,"shop/order_detail.html",{"order":get_object_or_404(request.user.orders.prefetch_related("items"),pk=pk)})
