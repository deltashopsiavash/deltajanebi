from django import template

from shop.services.wallet import wallet_balance as get_wallet_balance

register = template.Library()


@register.filter(name="wallet_balance")
def wallet_balance_filter(user):
    if not user or not getattr(user, "is_authenticated", False):
        return 0
    try:
        return get_wallet_balance(user.pk)
    except Exception:
        return 0
