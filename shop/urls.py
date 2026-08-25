from django.urls import path

from . import payment_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("category/<str:slug>/", views.category, name="category"),
    path("p/<str:slug>/", views.product_detail, name="product_detail"),
    path("register/", views.register, name="register"),
    path("cart/", views.cart_view, name="cart"),
    path("cart/data/", views.cart_data, name="cart_data"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/set/<int:product_id>/", views.cart_set, name="cart_set"),
    path("cart/discount/", views.cart_discount, name="cart_discount"),
    path("cart/discount/remove/", views.cart_discount_remove, name="cart_discount_remove"),
    path("checkout/", views.checkout, name="checkout"),
    path("payment/card/<int:pk>/", payment_views.card_payment, name="card_payment"),
    path("payment/zarinpal/callback/", views.zarinpal_callback, name="zarinpal_callback"),
    path("terms/", views.terms, name="terms"),
    path("account/", views.account_profile, name="account_profile"),
    path("account/orders/", views.account_orders, name="account_orders"),
    path("account/orders/<int:pk>/", views.account_order_detail, name="account_order_detail"),
]
