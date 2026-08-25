from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from shop.models import User
from shop.services.telegram_notify import notify_admins


def short_customer_code(user):
    return f"#{1000 + int(user.pk)}"


@receiver(post_save, sender=User)
def customer_created(sender, instance, created, **kwargs):
    if not instance.pk or instance.is_staff:
        return

    desired = short_customer_code(instance)
    if instance.customer_code != desired:
        User.objects.filter(pk=instance.pk).update(customer_code=desired)
        instance.customer_code = desired

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
