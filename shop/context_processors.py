from .models import Category,SiteSetting
def store_context(request):
    try: settings=SiteSetting.load()
    except Exception: settings=None
    cart=request.session.get("cart",{}) if hasattr(request,"session") else {}
    return {"store_settings":settings,"nav_categories":Category.objects.filter(is_active=True)[:12] if settings else [],"cart_count":sum(int(v) for v in cart.values())}
