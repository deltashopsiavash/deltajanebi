from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Banner, Category, DiscountCode, Order, OrderItem, Product, SiteSetting, SocialLink, SourceSite, TrustBadge, User


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
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_staff", "is_active")}),)
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


@admin.register(SourceSite)
class SourceSiteAdmin(admin.ModelAdmin):
    list_display = ("name", "hostname", "base_url", "bulk_import_enabled", "is_active", "last_bulk_sync_at")
    list_filter = ("is_active", "bulk_import_enabled")
    search_fields = ("name", "hostname", "base_url", "brand_terms")


@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    list_display = ("label", "platform", "url", "is_active", "order")
    list_filter = ("platform", "is_active")


@admin.register(TrustBadge)
class TrustBadgeAdmin(admin.ModelAdmin):
    list_display = ("badge_type", "target_url", "is_active")
    list_filter = ("badge_type", "is_active")


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "order", "created_at")
    list_filter = ("is_active",)


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "is_active", "starts_at", "ends_at")
    list_filter = ("discount_type", "is_active")
    search_fields = ("code",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "payment_method", "payment_status", "status", "total", "created_at")
    list_filter = ("payment_method", "payment_status", "status")
    search_fields = ("id", "full_name", "phone", "zarinpal_ref_id")


admin.site.register(SiteSetting)
admin.site.register(OrderItem)
