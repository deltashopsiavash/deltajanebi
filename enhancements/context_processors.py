from django.utils import timezone

from .models import ProductStory


def enhancement_context(request):
    try:
        stories = ProductStory.objects.filter(is_active=True, expires_at__gt=timezone.now()).order_by("sort_order", "-id")[:30]
    except Exception:
        stories = []
    return {"product_stories": stories}
