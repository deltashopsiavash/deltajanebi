from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from shop.models import User
from shop.services.telegram_notify import notify_admins


def _next_customer_number():
    maximum = 1000
    for value in User.objects.filter(is_staff=False, customer_code__startswith="#").values_list("customer_code", flat=True):
        try:
            maximum = max(maximum, int(str(value)[1:]))
        except (TypeError, ValueError):
            continue
    return maximum + 1


def assign_short_customer_code(user):
    if not user.pk or user.is_staff:
        return user.customer_code or ""
    if str(user.customer_code or "").startswith("#"):
        return user.customer_code

    for _ in range(8):
        desired = f"#{_next_customer_number()}"
        try:
            with transaction.atomic():
                User.objects.filter(pk=user.pk).update(customer_code=desired)
            user.customer_code = desired
            return desired
        except IntegrityError:
            continue
    raise IntegrityError("Could not allocate unique customer code")


@receiver(post_save, sender=User)
def customer_created(sender, instance, created, **kwargs):
    if not instance.pk or instance.is_staff:
        return

    desired = assign_short_customer_code(instance)

    if not created:
        return

    joined = timezone.localtime(instance.date_joined).strftime("%Y/%m/%d - %H:%M:%S")
    full_name = instance.get_full_name().strip() or "ثبت نشده"
    notify_admins(
        "🆕 عضویت جدید در سایت\n\n"
        f"شماره مشتری: {desired}\n"
        f"نام و نام خانوادگی: {full_name}\n"
        f"ایمیل: {instance.email}\n"
        f"شماره تلفن: {instance.phone or '-'}\n"
        f"تاریخ عضویت: {joined} به وقت ایران"
    )
