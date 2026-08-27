import os
from html import escape
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

from shop.models import SiteSetting


def _from_email():
    return getattr(settings, "DEFAULT_FROM_EMAIL", None) or None


def _public_base_url(request=None):
    if request is not None:
        return request.build_absolute_uri("/")
    domain = str(os.environ.get("DOMAIN") or getattr(settings, "DOMAIN", "") or "").strip().strip("/")
    if domain and domain not in {"localhost", "127.0.0.1"}:
        return f"https://{domain}/"
    return ""


def email_brand_context(request=None):
    store = SiteSetting.load()
    logo_url = ""
    raw = str(store.logo_src or "").strip()
    if raw:
        if raw.startswith("http://") or raw.startswith("https://"):
            logo_url = raw
        else:
            base = _public_base_url(request)
            if base:
                logo_url = urljoin(base, raw)
    return {"store": store, "logo_url": logo_url}


def send_otp_email(user, otp):
    context = email_brand_context()
    store = context["store"]
    subject = f"کد تأیید عضویت در {store.store_name}"
    text = (
        f"کد تأیید ایمیل شما: {otp.code}\n\n"
        "این کد ۱۰ دقیقه معتبر است.\n"
        "اگر شما درخواست ثبت‌نام نداده‌اید، این پیام را نادیده بگیرید."
    )
    context.update(user=user, code=otp.code)
    html = render_to_string("emails/otp.html", context)
    message = EmailMultiAlternatives(subject, text, _from_email(), [user.email])
    message.attach_alternative(html, "text/html")
    return message.send(fail_silently=False)


def send_password_reset_email(request, user):
    form = PasswordResetForm({"email": user.email})
    if not form.is_valid():
        raise ValueError("invalid_reset_email")
    form.save(
        request=request,
        use_https=request.is_secure(),
        from_email=_from_email(),
        email_template_name="registration/password_reset_email.txt",
        html_email_template_name="emails/password_reset.html",
        subject_template_name="registration/password_reset_subject.txt",
        extra_email_context=email_brand_context(request),
    )


def send_broadcast_email(subject, body, recipients):
    addresses = []
    seen = set()
    for value in recipients:
        email = str(value or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            addresses.append(email)
    if not addresses:
        return 0

    context = email_brand_context()
    context["body_html"] = escape(str(body or "").strip()).replace("\n", "<br>")
    html = render_to_string("emails/broadcast.html", context)
    text = str(body or "").strip()
    sent = 0
    # BCC protects the customer list from being exposed to other recipients.
    for index in range(0, len(addresses), 60):
        batch = addresses[index:index + 60]
        message = EmailMultiAlternatives(str(subject or "").strip()[:180], text, _from_email(), [], bcc=batch)
        message.attach_alternative(html, "text/html")
        if message.send(fail_silently=False):
            sent += len(batch)
    return sent
