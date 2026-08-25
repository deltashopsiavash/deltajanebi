from django.test import TestCase

from shop.management.commands import telegram_bot_v8
from shop.models import Category, Product, SiteSetting, SourceSite


class V8StorefrontTests(TestCase):
    def test_home_latest_products_excludes_unavailable_products_and_formats_price(self):
        Product.objects.create(name="پاوربانک ناموجود", price=230000, stock=0, is_active=True)
        Product.objects.create(name="پاوربانک موجود", price=230000, stock=2, is_active=True)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "پاوربانک ناموجود")
        self.assertContains(response, "پاوربانک موجود")
        self.assertContains(response, "230,000")

    def test_catalog_shows_unavailable_when_hide_setting_is_off(self):
        settings = SiteSetting.load()
        settings.hide_out_of_stock = False
        settings.save(update_fields=["hide_out_of_stock"])
        Product.objects.create(name="کالای ناموجود تست", price=230000, stock=0, is_active=True)
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "کالای ناموجود تست")
        self.assertContains(response, "ناموجود")
        self.assertContains(response, "230,000")

    def test_catalog_hides_unavailable_when_hide_setting_is_on(self):
        settings = SiteSetting.load()
        settings.hide_out_of_stock = True
        settings.save(update_fields=["hide_out_of_stock"])
        Product.objects.create(name="کالای ناموجود مخفی", price=230000, stock=0, is_active=True)
        response = self.client.get("/search/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "کالای ناموجود مخفی")

    def test_home_newest_products_are_limited_to_ten_and_horizontal(self):
        for index in range(12):
            Product.objects.create(name=f"محصول جدید {index}", price=100000 + index, stock=1)
        response = self.client.get("/")
        self.assertContains(response, "latest-products-strip")
        products = list(response.context["products"])
        self.assertEqual(len(products), 10)

    def test_nested_categories_are_prioritized_in_mobile_navigation(self):
        plain = Category.objects.create(name="بدون زیرشاخه", slug="plain")
        parent = Category.objects.create(name="دارای زیرشاخه", slug="parent")
        Category.objects.create(name="فرزند", slug="child", parent=parent)
        response = self.client.get("/")
        nav = response.context["nav_categories"]
        self.assertEqual(nav[0].pk, parent.pk)
        self.assertEqual(nav[1].pk, plain.pk)


class V8TelegramMenuTests(TestCase):
    def test_main_and_nested_management_menus_are_reorganized(self):
        main = [b.text for row in telegram_bot_v8.main_menu().inline_keyboard for b in row]
        products = [b.text for row in telegram_bot_v8.product_management_menu().inline_keyboard for b in row]
        admin = [b.text for row in telegram_bot_v8.admin_management_menu().inline_keyboard for b in row]
        settings = [b.text for row in telegram_bot_v8.settings_menu().inline_keyboard for b in row]

        self.assertIn("🛍 مدیریت محصولات", main)
        self.assertIn("🧭 تنظیمات مدیریتی", main)
        self.assertIn("📊 تمامی محصولات", main)
        self.assertIn("🔗 محصولات خاص", products)
        self.assertIn("🧰 محصولات عادی", products)
        self.assertIn("🔎 جستجوی محصول", products)
        self.assertIn("⭐ پیشنهادهای فعال", products)
        self.assertIn("📂 دسته‌بندی‌ها", products)
        self.assertIn("📦 سفارش‌ها", admin)
        self.assertIn("🔄 همگام‌سازی همه", admin)
        self.assertIn("🌐 ثبت سایت", admin)
        self.assertIn("🧹 پاکسازی محصولات", admin)
        self.assertIn("🛡 نمادها", settings)
        self.assertIn("☎️ تلفن", settings)
        self.assertIn("📝 توضیحات و فوتر", settings)

    def test_all_products_report_splits_sources_and_stock(self):
        site = SourceSite.objects.create(name="منبع تست", base_url="https://source.example", hostname="source.example")
        Product.objects.create(name="موجود", price=100000, stock=3, source_type=Product.SYNCED, source_url="https://source.example/product/1")
        Product.objects.create(name="ناموجود", price=120000, stock=0, source_type=Product.SYNCED, source_url="https://source.example/product/2")
        report = telegram_bot_v8._all_products_report()
        self.assertIn(site.base_url, report)
        self.assertIn("تعداد محصول: 2", report)
        self.assertIn("محصولات موجود: 1", report)
        self.assertIn("ناموجود: 1", report)

    def test_change_report_contains_old_new_source_and_store_prices(self):
        site = SourceSite.objects.create(name="منبع تست", base_url="https://source.example", hostname="source.example")
        product = Product.objects.create(name="کابل", price=150000, source_price=120000, stock=5, source_type=Product.SYNCED, source_url="https://source.example/product/1")
        lines = telegram_bot_v8._change_lines(site, product, False, {"source_price": (100000, 120000), "price": (130000, 150000), "stock": (4, 5)})
        joined = "\n".join(lines)
        self.assertIn("100,000", joined)
        self.assertIn("120,000", joined)
        self.assertIn("130,000", joined)
        self.assertIn("150,000", joined)
        self.assertIn("موجودی قبلی: 4", joined)
