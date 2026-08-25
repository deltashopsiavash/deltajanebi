from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User,Category,Product,SiteSetting,Order,OrderItem
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model=User; ordering=("email",); list_display=("email","is_staff","is_active"); fieldsets=((None,{"fields":("email","password")}),("اطلاعات",{"fields":("first_name","last_name","phone")}),("دسترسی",{"fields":("is_active","is_staff","is_superuser","groups","user_permissions")}),("تاریخ‌ها",{"fields":("last_login","date_joined")})); add_fieldsets=((None,{"classes":("wide",),"fields":("email","password1","password2","is_staff","is_active")}),); search_fields=("email",)
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display=("name","price","stock","source_type","last_synced_at","is_active"); list_filter=("source_type","is_active","category"); search_fields=("name","sku","source_url")
admin.site.register(Category); admin.site.register(SiteSetting); admin.site.register(Order); admin.site.register(OrderItem)
