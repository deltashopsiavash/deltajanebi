from django.urls import path

from enhancements import account_views as enhanced_accounts
from enhancements.site_api_v15 import bot_api
from . import auth_views, views, wallet_checkout

urlpatterns = [
    path("", views.home, name="home"),
    path("api/bot/v1/", bot_api, name="bot_api_v1"),
    path("search/", views.search, name="search"),
    path("category/<str:slug>/", views.category, name="category"),
    path("p/<str:slug>/", views.product_detail, name="product_detail"),
    path("register/", enhanced_accounts.register, name="register"),
    path("verify-email/", enhanced_accounts.verify_email_code, name="verify_email_code"),
    path("auth/email-check/", auth_views.email_check, name="auth_email_check"),
    path("auth/login/", auth_views.login_ajax, name="auth_login_ajax"),
    path("notifications/read/", views.notifications_mark_read, name="notifications_mark_read"),
    path("cart/", views.cart_view, name="cart"),
    path("cart/data/", views.cart_data, name="cart_data"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/set/<int:product_id>/", views.cart_set, name="cart_set"),
    path("cart/discount/", views.cart_discount, name="cart_discount"),
    path("cart/discount/remove/", views.cart_discount_remove, name="cart_discount_remove"),
    path("checkout/", wallet_checkout.checkout, name="checkout"),
    path("payment/card/<int:pk>/", wallet_checkout.card_payment, name="card_payment"),
    path("payment/zarinpal/callback/", wallet_checkout.zarinpal_callback, name="zarinpal_callback"),
    path("terms/", views.terms, name="terms"),
    path("account/", views.account_profile, name="account_profile"),
    path("account/orders/", views.account_orders, name="account_orders"),
    path("account/orders/<int:pk>/", views.account_order_detail, name="account_order_detail"),
]
