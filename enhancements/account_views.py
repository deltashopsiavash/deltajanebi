import re

from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from shop.forms import RegisterForm
from shop.models import User

from .emailing import send_otp_email
from .models import EmailVerificationCode


def _normalize_code(value):
    value = str(value or "").strip()
    fa = "۰۱۲۳۴۵۶۷۸۹"
    ar = "٠١٢٣٤٥٦٧٨٩"
    value = value.translate(str.maketrans(fa + ar, "0123456789" * 2))
    return re.sub(r"\D", "", value)[:6]


def _safe_target(request):
    target = request.session.pop("registration_next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return "account_profile"


def register(request):
    if request.user.is_authenticated:
        return redirect("account_profile")

    next_url = request.GET.get("next") or request.POST.get("next") or ""
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        request.session["registration_next"] = next_url

    initial = {}
    email = (request.GET.get("email") or "").strip().lower()
    if email:
        initial["email"] = email
    form = RegisterForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.is_active = False
        user.save()
        otp = EmailVerificationCode.issue(user)
        request.session["pending_verification_user_id"] = user.id
        try:
            send_otp_email(user, otp)
        except Exception:
            messages.error(request, "حساب ساخته شد اما ارسال کد تأیید ممکن نشد. تنظیمات ایمیل را بررسی کنید و ارسال مجدد را بزنید.")
        return redirect("verify_email_code")
    return render(request, "registration/register.html", {"form": form, "next": next_url})


def verify_email_code(request):
    if request.user.is_authenticated:
        return redirect("account_profile")

    user_id = request.session.get("pending_verification_user_id")
    user = User.objects.filter(pk=user_id).first() if user_id else None
    if not user:
        messages.error(request, "درخواست تأیید ایمیل پیدا نشد. دوباره ثبت‌نام کنید.")
        return redirect("register")
    if user.is_active:
        request.session.pop("pending_verification_user_id", None)
        return redirect("login")

    otp = EmailVerificationCode.objects.filter(user=user).first()
    if not otp:
        otp = EmailVerificationCode.issue(user)
        try:
            send_otp_email(user, otp)
        except Exception:
            messages.error(request, "ارسال کد تأیید ممکن نشد.")

    if request.method == "POST" and request.POST.get("action") == "resend":
        seconds = int((timezone.now() - otp.last_sent_at).total_seconds()) if otp else 61
        if seconds < 60:
            messages.warning(request, f"برای ارسال دوباره {60 - seconds} ثانیه صبر کنید.")
        else:
            otp = EmailVerificationCode.issue(user)
            try:
                send_otp_email(user, otp)
                messages.success(request, "کد جدید ارسال شد.")
            except Exception:
                messages.error(request, "ارسال کد جدید ممکن نشد.")
        return redirect("verify_email_code")

    error = ""
    if request.method == "POST":
        code = _normalize_code(request.POST.get("code"))
        otp = EmailVerificationCode.objects.filter(user=user).first()
        if not otp:
            error = "کد تأیید پیدا نشد؛ ارسال مجدد را بزنید."
        elif otp.is_expired:
            error = "کد تأیید منقضی شده است؛ کد جدید دریافت کنید."
        elif otp.attempts >= 6:
            error = "تعداد تلاش‌های ناموفق زیاد شده است؛ کد جدید دریافت کنید."
        elif len(code) != 6 or not otp.matches(code):
            otp.attempts += 1
            otp.save(update_fields=["attempts", "updated_at"])
            error = "کد تأیید نادرست است."
        else:
            user.is_active = True
            user.save(update_fields=["is_active"])
            otp.delete()
            request.session.pop("pending_verification_user_id", None)
            login(request, user, backend="shop.backends.EmailBackend")
            messages.success(request, "ایمیل شما تأیید شد و حساب فعال شد.")
            return redirect(_safe_target(request))

    return render(request, "registration/verification_code.html", {"email": user.email, "verification_error": error, "otp_expires_at": otp.expires_at if otp else None})
