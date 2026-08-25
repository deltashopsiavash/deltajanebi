from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    # Django's <slug:...> converter is ASCII-only. Our model intentionally
    # generates Persian/Unicode slugs, so use <str:...> (matches any non-slash
    # path segment) and let the database lookup validate the actual slug.
    path("category/<str:slug>/", views.category, name="category"),
    path("p/<str:slug>/", views.product_detail, name="product_detail"),
    path("register/", views.register, name="register"),
    path("cart/", views.cart_view, name="cart"),
    path("cart/add/<int:product_id>/", views.cart_add, name="cart_add"),
    path("cart/set/<int:product_id>/", views.cart_set, name="cart_set"),
    path("checkout/", views.checkout, name="checkout"),
    path("account/orders/", views.account_orders, name="account_orders"),
    path("account/orders/<int:pk>/", views.account_order_detail, name="account_order_detail"),
]
