from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.http import JsonResponse

from .models import User


def email_check(request):
    email = (request.GET.get("email") or "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"ok": False, "error": "ایمیل معتبر وارد کنید."}, status=400)

    return JsonResponse({
        "ok": True,
        "exists": User.objects.filter(email__iexact=email, is_active=True).exists(),
    })
