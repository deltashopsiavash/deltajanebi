from django.contrib.auth import authenticate, login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
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
        "exists": User.objects.filter(email__iexact=email).exists(),
    })


def login_ajax(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "درخواست نامعتبر است."}, status=405)
    email = (request.POST.get("email") or request.POST.get("username") or "").strip().lower()
    password = request.POST.get("password") or ""
    try:
        validate_email(email)
    except ValidationError:
        return JsonResponse({"ok": False, "error": "ایمیل معتبر وارد کنید."}, status=400)
    user = authenticate(request, email=email, password=password)
    if user is None:
        return JsonResponse({"ok": False, "error": "ایمیل یا رمز عبور درست نیست. دوباره بررسی کن."}, status=400)
    if not user.is_active:
        return JsonResponse({"ok": False, "error": "این حساب کاربری غیرفعال است."}, status=403)
    login(request, user)
    next_path = request.POST.get("next") or "/"
    if not str(next_path).startswith("/"):
        next_path = "/"
    return JsonResponse({"ok": True, "redirect": next_path})
