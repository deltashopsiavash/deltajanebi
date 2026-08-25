from django import template

from shop.services.order_workflow import payment_method_label
from shop.services.wallet import external_payable, order_wallet_info

register = template.Library()


@register.filter
def money(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value


@register.filter
def wallet_amount(order):
    try:
        return order_wallet_info(order.id)["wallet_amount"]
    except Exception:
        return 0


@register.filter
def wallet_refunded(order):
    try:
        return order_wallet_info(order.id)["refunded"]
    except Exception:
        return False


@register.filter
def external_due(order):
    try:
        return external_payable(order)
    except Exception:
        return int(getattr(order, "total", 0) or 0)


@register.filter
def payment_display(order):
    try:
        return payment_method_label(order)
    except Exception:
        return order.get_payment_method_display()
