from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Banner, Category, Order, OrderItem, Product, SiteSetting, SocialLink, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User
    ordering = ("email",)
    list_display = ("email", "is_staff", "is_active")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("اطلاعات", {"fields": ("first_name", "last_name", "phone")}),
        ("دسترسی", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("تاریخ‌ها", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_staff", "is_active")}),
    )
    search_fields = ("email",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "order", "is_active")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("public_code", "name", "category", "price", "stock", "source_type", "last_synced_at", "is_active")
    list_filter = ("source_type", "is_active", "category")
    search_fields = ("public_code", "name", "sku", "source_url")
    readonly_fields = ("public_code", "last_synced_at", "sync_error")


@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    list_display = ("label", "platform", "url", "is_active", "order")
    list_filter = ("platform", "is_active")


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "order", "created_at")
    list_filter = ("is_active",)


admin.site.register(SiteSetting)
admin.site.register(Order)
admin.site.register(OrderItem)
